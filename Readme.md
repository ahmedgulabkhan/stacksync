## Docker Commands for Local Running

- `docker build -t flask-api .`
- `docker run -d -p 8080:8080 --name flask-api flask-api`

## Docker Commands for Production Deployment
- `docker buildx build -t flask-api --platform linux/amd64 .` (for Mac silicon chips, otherwise use the same command as above)
- `docker tag flask-api gcr.io/<your-gcp-project>/flask-api`
- `docker push gcr.io/<your-gcp-project>/flask-api`

## cURL Example
```
curl --location 'https://flask-api-253669422720.europe-west1.run.app/execute' \
--header 'Content-Type: application/json' \
--data '{
  "script": "import numpy as np\ndef main():\n    x = np.array([1, 2, 3])\n    print(x)\n    print('\''Hello World'\'')\n    return 123"
}'
```