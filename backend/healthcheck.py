import json
import sys
import urllib.request

URL = (
 "http://127.0.0.1:8000"
 "/api/v1/health"
)

def main():
 with urllib.request.urlopen(
  URL,
  timeout=3
 ) as response:
  payload = json.loads(
   response.read()
  )
  if response.status != 200:
   return 1
  if payload.get("status") != "ok":
   return 1
  if payload.get("model_loaded") is not True:
   return 1
  return 0

if __name__ == "__main__":
 try:
  sys.exit(main())
 except Exception:
  sys.exit(1)
