build:
	docker build -t backend ./backend
	docker build -t speech-to-text ./speech-to-text

up:
	docker run -d --name backend -p 8000:8000 backend
	docker run -d --name stt -p 8001:8000 speech-to-text

down:
	docker stop backend stt || true
	docker rm backend stt || true

restart: down up