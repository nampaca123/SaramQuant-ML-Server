.PHONY: check

check:
	cd infra && terraform init -backend=false -input=false >/dev/null \
	  && terraform fmt -check -recursive && terraform validate
