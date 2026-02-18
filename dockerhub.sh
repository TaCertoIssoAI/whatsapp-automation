#!/bin/bash
set -e

DOCKER_USER="tacertoissoai"
IMAGE_NAME="whatsapp-integration"
VERSION=$(date +%Y%m%d-%H%M%S)

echo "🐳 Building Docker image..."

docker build \
  -t ${IMAGE_NAME}:latest \
  -t ${IMAGE_NAME}:${VERSION} \
  .

echo "🏷️ Tagging images..."

docker tag ${IMAGE_NAME}:latest ${DOCKER_USER}/${IMAGE_NAME}:latest
docker tag ${IMAGE_NAME}:${VERSION} ${DOCKER_USER}/${IMAGE_NAME}:${VERSION}

echo "🚀 Pushing to Docker Hub..."

docker push ${DOCKER_USER}/${IMAGE_NAME}:latest
docker push ${DOCKER_USER}/${IMAGE_NAME}:${VERSION}

echo ""
echo "✅ DONE!"
