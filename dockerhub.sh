#!/bin/bash
set -e

ENV=$1

if [ "$ENV" != "production" ] && [ "$ENV" != "develop" ]; then
  echo "❌ Erro: Ambiente inválido."
  echo "Uso: ./dockerhub.sh [production|develop]"
  exit 1
fi

DOCKER_USER="tacertoissoai"
IMAGE_NAME="whatsapp-integration"
VERSION=$(date +%Y%m%d-%H%M%S)
VERSION_TAG="${ENV}-${VERSION}"

echo "🐳 Building Docker image for ${ENV} (linux/amd64)..."

docker build \
  --platform linux/amd64 \
  -t ${IMAGE_NAME}:${ENV} \
  -t ${IMAGE_NAME}:${VERSION_TAG} \
  .

echo "🏷️ Tagging images..."

docker tag ${IMAGE_NAME}:${ENV} ${DOCKER_USER}/${IMAGE_NAME}:${ENV}
docker tag ${IMAGE_NAME}:${VERSION_TAG} ${DOCKER_USER}/${IMAGE_NAME}:${VERSION_TAG}

echo "🚀 Pushing to Docker Hub..."

docker push ${DOCKER_USER}/${IMAGE_NAME}:${ENV}
docker push ${DOCKER_USER}/${IMAGE_NAME}:${VERSION_TAG}

echo ""
echo "✅ DONE! Pushed tags: ${ENV} and ${VERSION_TAG}"
