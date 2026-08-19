import os
from dotenv import load_dotenv
load_dotenv()
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_SESSION_TOKEN = os.getenv("AWS_SESSION_TOKEN")
AWS_REGION = os.getenv("AWS_REGION")
MODEL_ID = os.getenv("BEDROCK_CHAT_MODEL")
EMBEDDING_MODEL = os.getenv( "BEDROCK_EMBEDDING_MODEL")
GET_DATA_URL = os.getenv("GET_DATA_URL")
KEY = os.getenv("KEY")
VALUE = os.getenv("VALUE")