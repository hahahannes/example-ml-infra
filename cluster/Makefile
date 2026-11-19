NAMESPACE ?= argo-cd
MASTER    ?=
WORKERS   ?=

# Use the kubeconfig written by install-k3s.sh if present
ifneq ($(wildcard kubeconfig),)
export KUBECONFIG ?= $(CURDIR)/kubeconfig
endif

.PHONY: all k3s argocd services argocd-ui mlflow-ui

all: argocd services

k3s:
	@test -n "$(MASTER)" || { echo "Usage: make k3s MASTER=<ip> [WORKERS=\"<ip> ...\"]"; exit 1; }
	./install-k3s.sh $(MASTER) $(WORKERS)

argocd:
	helm dependency build base-charts/argo-cd
	helm upgrade --install argo-cd base-charts/argo-cd \
		--namespace $(NAMESPACE) --create-namespace \
		-f base-charts/argo-cd/values.yaml --wait

services:
	kubectl apply -n $(NAMESPACE) -f main.yaml

argocd-ui:
	@echo "Argo CD UI: http://localhost:8080  (user: admin)"
	@echo "password: $$(kubectl -n $(NAMESPACE) get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)"
	kubectl -n $(NAMESPACE) port-forward svc/argo-cd-argocd-server 8080:80

mlflow-ui:
	@echo "MLflow UI: http://localhost:5000"
	kubectl -n mlflow port-forward svc/mlflow 5000:5000
