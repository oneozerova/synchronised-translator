include .env
export

NETWORK=app-net

build:
	docker build -t backend ./backend
	docker build -t speech-to-text ./speech-to-text

network:
	docker network inspect $(NETWORK) >/dev/null 2>&1 || docker network create $(NETWORK)

up: network
	docker run --env-file .env -d --name backend --network $(NETWORK) -p $(BACKEND_PORT):8000 backend
	docker run --env-file .env -d --name stt --network $(NETWORK) -p $(STT_PORT):8000 speech-to-text

down:
	docker stop backend stt || true
	docker rm backend stt || true

restart: down up

rebuild: down build up