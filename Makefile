include .env
export

NETWORK=app-net

build:
	docker build -t backend ./backend
	docker build -t speech-to-text ./speech-to-text
	docker build -t translator ./translator

network:
	docker network inspect $(NETWORK) >/dev/null 2>&1 || docker network create $(NETWORK)

up: network
	docker run --env-file .env -d --name backend --network $(NETWORK) -p $(BACKEND_PORT):8000 backend
	docker run --env-file .env -d --name stt --network $(NETWORK) -p $(STT_PORT):8000 speech-to-text
	docker run --env-file .env -d --name translator --network $(NETWORK) -p $(TRANSLATOR_PORT):8000 translator

down:
	docker stop backend stt translator || true
	docker rm backend stt translator || true

restart: down up

rebuild: down build up