# client.py
import boto3
from langchain_aws import BedrockEmbeddings                # kept ONLY for embeddings
from agent_framework_bedrock import BedrockChatClient      # MAF native chat

from config import (
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    AWS_SESSION_TOKEN,
    AWS_REGION,
    MODEL_ID,
    EMBEDDING_MODEL,
)

# ----------------------------------------------------------------------
# Shared boto3 session (used by BOTH chat client and embeddings)
# ----------------------------------------------------------------------
session_kwargs = {
    "aws_access_key_id": AWS_ACCESS_KEY_ID,
    "aws_secret_access_key": AWS_SECRET_ACCESS_KEY,
    "region_name": AWS_REGION,
}
if AWS_SESSION_TOKEN and AWS_SESSION_TOKEN.strip():
    session_kwargs["aws_session_token"] = AWS_SESSION_TOKEN

session = boto3.Session(**session_kwargs)
bedrock_client = session.client("bedrock-runtime")

# ----------------------------------------------------------------------
# MAF NATIVE chat client (powers all agents)
#   - model   (NOT model_id)
#   - reuse the boto3_session so creds/region are shared
# ----------------------------------------------------------------------
chat_client = BedrockChatClient(
    model=MODEL_ID,
    boto3_session=session,       # <-- shares your authenticated session
)

# ----------------------------------------------------------------------
# LangChain embeddings (RAG only)
# ----------------------------------------------------------------------
embeddings = BedrockEmbeddings(
    client=bedrock_client,
    model_id=EMBEDDING_MODEL,
)