import os

import boto3
import pandas as pd
from datasets import load_dataset
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for
import logging

from src.errors import DynamoOperationError, DynamoDuplicatedError
from src.storage import DynamoDB
from src.youtube import ProcessYoutube, Youtube

load_dotenv()

app = Flask(__name__)

# SpringBoot AutoWire가 없어서 수동 주입 준비
# DynamoDB와 S3 클라이언트 설정
s3_object = boto3.client('s3', region_name='ap-northeast-2')
dynamodb_object = boto3.resource('dynamodb', region_name='ap-northeast-2')
table_object = dynamodb_object.Table('Subtitle-Ondemand')

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# BUCKET_NAME = 'subtitle-collection'
VTT_DIRECTORY = './vtt'
DEBUG = bool(os.getenv('DEBUG'))
if DEBUG is not True:
    os.makedirs(VTT_DIRECTORY, exist_ok=True)


def pagination(data, page, per_page):
    total_pages = len(data) // per_page + 1
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    paginated_data = data[start_idx:end_idx]
    prev_page = page - 1 if page > 1 else None
    next_page = page + 1 if page < total_pages else None
    return paginated_data, prev_page, next_page, total_pages


@app.route('/automation', methods=['GET'])
def automation():
    return render_template('automation.html')


@app.route('/yt-dlp-search', methods=['POST'])
def yt_dlp_search():
    leetcode_number = request.form['leetcode_number']
    datasets = pd.DataFrame(load_dataset("greengerong/leetcode")['train'])
    title = datasets.loc[int(leetcode_number) - 1, 'title']

    query = f'leetcode {leetcode_number}'

    videos = Youtube.search_bulk(keyword=query, max_results=100)

    return render_template('automation_search_result.html', query=query, leetcode_number=leetcode_number, videos=videos)


@app.route('/add_one', methods=['POST'])
def add_one():
    error_message = None
    success_message = None

    video_id = request.form.get('video_id', None)
    leetcode_number = request.form.get('leetcode_number', None)

    if video_id is None:
        error_message = 'No video id provided.'
        return render_template('automation_add_result.html', error_message=error_message)
    if leetcode_number is None:
        error_message = f'Leetcode id not found.'
        return render_template('automation_add_result.html', error_message=error_message)

    youtube_url = f'https://youtu.be/{video_id}'

    try:
        ProcessYoutube(
            # s3=S3(storage_object=s3_object),
            dynamo_table=DynamoDB(storage_object=table_object),
            youtube_url=youtube_url,
            vtt_directory=VTT_DIRECTORY,
            leetcode_number=int(leetcode_number)
        )
        success_message = '처리 완료되었습니다.'
    except DynamoOperationError as e:
        error_message = f'중복된 영상입니다: {e}'
    except DynamoDuplicatedError as e:
        error_message = f'중복된 영상입니다: {e}'
    except Exception as e:
        # 기타 등등 잡다한 에러 처리하는 곳.
        error_message = f'에러: {e}'

    return render_template('automation_add_result.html',
                           success_message=success_message, error_message=error_message)


@app.route('/update_post/<video_id>', methods=['POST'])
def update_post(video_id):
    try:
        # 새로운 content
        new_content = request.form.get('content')

        # 실제로 업데이트 작업을 수행하는 코드
        response = table_object.update_item(
            Key={
                'video_id': video_id
            },
            UpdateExpression='SET content = :val',
            ExpressionAttributeValues={
                ':val': new_content
            }
        )
        print("Post updated successfully")

        # 업데이트가 성공하면 post 페이지로 리다이렉트합니다.
        return redirect(url_for('post', video_id=video_id))
    except Exception as e:
        print(f"Error updating post: {e}")
        # 실패할 경우 에러 메시지를 출력하고 이전 페이지로 리다이렉트합니다.
        return redirect(request.referrer or url_for('board'))  # 이전 페이지로 리다이렉트


@app.route('/delete_post/<video_id>', methods=['POST'])
def delete_post(video_id):
    try:
        # 여기에 삭제 작업을 수행하는 코드를 추가하세요
        table_object.delete_item(
            Key={
                'video_id': video_id
            }
        )
        print("Post deleted successfully")
        # 삭제가 성공하면 게시판 페이지로 리다이렉트합니다.
        return redirect(url_for('board'))
    except Exception as e:
        print(f"Error deleting post: {e}")
        # 실패할 경우 에러 메시지를 출력하고 이전 페이지로 리다이렉트합니다.
        return redirect(request.referrer or url_for('board'))  # 이전 페이지로 리다이렉트


@app.route('/post/<video_id>')
def post(video_id):
    try:
        response = table_object.get_item(Key={'video_id': video_id})
        post = response.get('Item', {})

    except Exception as e:
        print(f"Error retrieving post from DynamoDB: {e}")
        post = {}

    return render_template('post.html', post=post)


@app.route('/count')
def count():
    try:
        # leetcode_number별 카운트를 저장할 딕셔너리 초기화
        count_by_leetcode_number = {i: 0 for i in range(1, 2001)}

        # DynamoDB 테이블 스캔
        response = table_object.scan()
        items = response['Items']

        # 항목들을 순회하면서 leetcode_number 별로 카운트
        for item in items:
            leetcode_number = int(item['leetcode_number'])
            count_by_leetcode_number[leetcode_number] += 1

        # LastEvaluatedKey를 확인하여 모든 데이터를 스캔했는지 확인
        while 'LastEvaluatedKey' in response:
            response = table_object.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
            items = response['Items']
            for item in items:
                leetcode_number = int(item['leetcode_number'])
                count_by_leetcode_number[leetcode_number] += 1

        logging.info(f"검색된 포스트의 총 갯수: {sum(count_by_leetcode_number.values())}")

        # 계산된 결과를 25개씩 묶어서 리스트로 변환하여 템플릿에 전달
        counts = list(count_by_leetcode_number.values())
        counts_chunks = [counts[i:i + 25] for i in range(0, len(counts), 25)]
        return render_template('count.html', counts_chunks=counts_chunks)
    except Exception as e:
        logging.error(f"Error searching by leetcode_number: {e}")
        return render_template('count.html', counts_chunks=[])


@app.route('/board')
def board():
    search_query = request.args.get('q') # leet code number 값
    search_field = request.args.get('search_field') # 검색 분류

    if not search_query or not search_field: # 검색 값이 없을 때
        try:
            # DynamoDB 테이블에서 모든 게시물 가져오기
            response = table_object.scan()
            all_posts = response['Items']
        except Exception as e:
            print(f"Error retrieving posts from DynamoDB: {e}")
            all_posts = []
    elif search_field == 'leetcode_number': # leet code number를 검색했을 때
        try:
            print('검색한 숫자 값: ', int(search_query))
            print('검색한 숫자 값 타입: ', type(int(search_query)))
            response = table_object.query(
                TableName='Subtitle-Ondemand',
                IndexName='leetcode_number-index',  # 생성한 글로벌 보조 인덱스 이름
                KeyConditionExpression='leetcode_number = :number',
                ExpressionAttributeValues={
                    ':number': int(search_query)
                }
            )
            all_posts = response['Items']
            logging.info(f"검색된 포스트의 총 갯수: {len(all_posts)}")
        except Exception as e:
            print(f"Error searching by leetcode_number: {e}")
            all_posts = []
    else:
        # 올바르지 않은 검색 필드를 선택한 경우
        return "올바른 검색 필드를 선택하세요 (title 또는 leetcode_number)."

    page = int(request.args.get('page', 1))  # 페이지 번호, 기본값은 1
    per_page = 10  # 페이지당 게시물 수

    posts, prev_page, next_page, total_pages = pagination(all_posts, page, per_page)

    return render_template('board.html', posts=posts, prev_page=prev_page, next_page=next_page, total_pages=total_pages,
                           search_query=search_query, search_field=search_field)


@app.route('/', methods=['GET', 'POST'])
def index():
    error_message = None
    success_message = None

    if request.method == 'POST':
        youtube_url = request.form.get('youtube_url')
        leetcode_number = request.form.get('leetcode_number')
        try:
            ProcessYoutube(
                # s3=S3(storage_object=s3_object),
                dynamo_table=DynamoDB(storage_object=table_object),
                youtube_url=youtube_url,
                vtt_directory=VTT_DIRECTORY,
                leetcode_number=int(leetcode_number)
            )
            success_message = '처리 완료되었습니다.'
        except DynamoOperationError as e:
            error_message = f'중복된 영상입니다: {e}'
        except DynamoDuplicatedError as e:
            error_message = f'중복된 영상입니다: {e}'
        except Exception as e:
            # 기타 등등 잡다한 에러 처리하는 곳.
            error_message = f'에러: {e}'

    return render_template('index.html', error_message=error_message, success_message=success_message)


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=9090, debug=True)
