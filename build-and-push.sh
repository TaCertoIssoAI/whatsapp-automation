#!/bin/bash
# Script para build e push da imagem Docker
# Uso: ./build-and-push.sh [seu-usuario-dockerhub]

set -e

DOCKER_USER="${1:-seu-usuario}"
IMAGE_NAME="whatsapp-integration"
VERSION=$(date +%Y%m%d-%H%M%S)

echo "🐳 Building Docker image..."
sudo docker build -t ${IMAGE_NAME}:latest -t ${IMAGE_NAME}:${VERSION} .

echo "✅ Build completed!"
echo ""
echo "📋 Next steps:"
echo "1. Login to Docker Hub: docker login"
echo "2. Tag image: docker tag ${IMAGE_NAME}:latest ${DOCKER_USER}/${IMAGE_NAME}:latest"
echo "3. Push image: docker push ${DOCKER_USER}/${IMAGE_NAME}:latest"
echo ""
echo "Or run these commands automatically:"
echo "  docker tag ${IMAGE_NAME}:latest ${DOCKER_USER}/${IMAGE_NAME}:latest"
echo "  docker tag ${IMAGE_NAME}:${VERSION} ${DOCKER_USER}/${IMAGE_NAME}:${VERSION}"
echo "  docker push ${DOCKER_USER}/${IMAGE_NAME}:latest"
echo "  docker push ${DOCKER_USER}/${IMAGE_NAME}:${VERSION}"
