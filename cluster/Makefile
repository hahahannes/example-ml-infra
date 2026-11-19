NAMESPACE ?= argo-cd
MASTER    ?=
WORKERS   ?=
SSH_USER  ?= root
CLUSTER   ?= single-region
KUBECFG   ?= $(CURDIR)/kubeconfig
PYTHON    ?= python3
EPOCHS    ?=

# Use the kubeconfig written by install-k3s.sh if present
ifneq ($(wildcard kubeconfig),)
export KUBECONFIG ?= $(CURDIR)/kubeconfig
endif

.PHONY: all k3s worker argocd services argocd-ui mlflow-ui kfp-ui pipeline

all: argocd services

k3s:
	@test -n "$(MASTER)" || { echo "Usage: make k3s MASTER=<ip> [WORKERS=\"<ip> ...\"] [SSH_USER=<user>]"; exit 1; }
	SSH_USER=$(SSH_USER) ./install-k3s.sh $(MASTER) $(WORKERS)

# Join extra worker(s) to an existing cluster. MASTER defaults to the server in the kubeconfig;
# the agent gets the same k3s version as the master.
SSH = ssh -o StrictHostKeyChecking=accept-new
worker:
	@test -n "$(WORKERS)" || { echo "Usage: make worker WORKERS=\"<ip> ...\" [MASTER=<ip>] [SSH_USER=<user>]"; exit 1; }
	@master="$(MASTER)"; \
	[ -n "$$master" ] || master=$$(kubectl --kubeconfig $(KUBECFG) config view --minify -o jsonpath='{.clusters[0].cluster.server}' | sed -E 's#https://([^:/]+).*#\1#'); \
	[ -n "$$master" ] || { echo "Set MASTER=<ip>"; exit 1; }; \
	token=$$($(SSH) $(SSH_USER)@$$master "sudo cat /var/lib/rancher/k3s/server/node-token"); \
	version=$$($(SSH) $(SSH_USER)@$$master "k3s --version" | awk 'NR==1 {print $$3}'); \
	for w in $(WORKERS); do \
		echo ">>> Joining $$w to $$master (k3s $$version)"; \
		$(SSH) $(SSH_USER)@$$w "curl -sfL https://get.k3s.io | sudo INSTALL_K3S_VERSION=$$version K3S_URL=https://$$master:6443 K3S_TOKEN=$$token sh -" || exit 1; \
	done; \
	kubectl --kubeconfig $(KUBECFG) get nodes -o wide

argocd:
	@test -f $(KUBECFG) || { echo "Missing $(KUBECFG); run make k3s first or set KUBECFG=<path>"; exit 1; }
	helm dependency build base-charts/argo-cd
	helm upgrade --install argo-cd base-charts/argo-cd --kubeconfig $(KUBECFG) \
		--namespace $(NAMESPACE) --create-namespace \
		-f base-charts/argo-cd/values.yaml --wait
	@# Register the cluster from the kubeconfig in Argo CD under the name $(CLUSTER)
	@k="kubectl --kubeconfig $(KUBECFG) config view --raw --minify -o jsonpath"; \
	server=$$($$k='{.clusters[0].cluster.server}'); \
	ca=$$($$k='{.clusters[0].cluster.certificate-authority-data}'); \
	cert=$$($$k='{.users[0].user.client-certificate-data}'); \
	key=$$($$k='{.users[0].user.client-key-data}'); \
	echo ">>> Registering cluster $(CLUSTER) ($$server) in Argo CD"; \
	kubectl --kubeconfig $(KUBECFG) -n $(NAMESPACE) create secret generic cluster-$(CLUSTER) \
		--from-literal=name=$(CLUSTER) \
		--from-literal=server="$$server" \
		--from-literal=config="{\"tlsClientConfig\":{\"caData\":\"$$ca\",\"certData\":\"$$cert\",\"keyData\":\"$$key\"}}" \
		--dry-run=client -o yaml \
	| kubectl label --local -f - argocd.argoproj.io/secret-type=cluster -o yaml \
	| kubectl --kubeconfig $(KUBECFG) apply -f -

services:
	kubectl apply -n $(NAMESPACE) -f main.yaml

argocd-ui:
	@echo "Argo CD UI: http://localhost:8080  (user: admin)"
	@echo "password: $$(kubectl -n $(NAMESPACE) get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)"
	kubectl -n $(NAMESPACE) port-forward svc/argo-cd-argocd-server 8080:80

mlflow-ui:
	@echo "MLflow UI: http://localhost:5000"
	kubectl -n mlflow port-forward svc/mlflow 5000:5000

kfp-ui:
	@echo "Kubeflow Pipelines UI: http://localhost:8081"
	kubectl -n kubeflow port-forward svc/ml-pipeline-ui 8081:80

# Compile ../pipeline/kfp_pipeline.py and start a run (needs: pip install kfp)
pipeline:
	cd ../pipeline && $(PYTHON) kfp_pipeline.py --host http://localhost:8081 $(if $(EPOCHS),--epochs $(EPOCHS))
