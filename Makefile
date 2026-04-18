include .env
export

build:
	docker build -t backend ./backend
	docker build -t speech-to-text ./speech-to-text

up:
	docker run --env-file .env -d --name backend -p $(BACKEND_PORT):8000 backend
	docker run --env-file .env -d --name stt -p $(STT_PORT):8000 speech-to-text

down:
	docker stop backend stt || true
	docker rm backend stt || true

restart: down up