FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# системные зависимости (минимум)
RUN apt-get update && apt-get install -y curl build-essential && rm -rf /var/lib/apt/lists/*

# install uv
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# копируем dependency files
COPY pyproject.toml uv.lock ./

# install dependencies через uv
RUN uv sync --frozen --no-dev

# копируем код
COPY . .

# запуск
CMD ["uv", "run", "uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]