"""AWS Lambda entry point (Lambda Function URL, payload format 2.0).

One function serves both the static dashboard and the JSON API, so no S3 bucket or API Gateway
is needed. Handler setting in the Lambda console: backend.lambda_handler.handler
"""
import base64

from backend.app import handle_request


def handler(event, context=None):
    http = (event.get("requestContext") or {}).get("http") or {}
    method = http.get("method", "GET")
    path = event.get("rawPath") or http.get("path") or "/"
    body = event.get("body") or ""
    raw = base64.b64decode(body) if event.get("isBase64Encoded") else body.encode("utf-8")
    status, headers, payload = handle_request(method, path, raw)
    text_like = headers.get("Content-Type", "").startswith(("text/", "application/json", "application/javascript", "image/svg"))
    if text_like:
        return {"statusCode": status, "headers": headers, "body": payload.decode("utf-8")}
    return {"statusCode": status, "headers": headers, "body": base64.b64encode(payload).decode("ascii"), "isBase64Encoded": True}
