# AWS CodeArtifact 設定（要編集）
AWS_ACCOUNT_ID := 742967853267
AWS_REGION := ap-northeast-1
CODEARTIFACT_DOMAIN := alexandria
CODEARTIFACT_REPO := pypi-store-xenox

# 導出値
CODEARTIFACT_URL := https://$(CODEARTIFACT_DOMAIN)-$(AWS_ACCOUNT_ID).d.codeartifact.$(AWS_REGION).amazonaws.com/pypi/$(CODEARTIFACT_REPO)/simple/
PUBLISH_URL := https://$(CODEARTIFACT_DOMAIN)-$(AWS_ACCOUNT_ID).d.codeartifact.$(AWS_REGION).amazonaws.com/pypi/$(CODEARTIFACT_REPO)/

.PHONY: build publish login clean

build:
	uv build

login:
	$(eval TOKEN := $(shell aws codeartifact get-authorization-token \
		--domain $(CODEARTIFACT_DOMAIN) \
		--domain-owner $(AWS_ACCOUNT_ID) \
		--region $(AWS_REGION) \
		--query authorizationToken \
		--output text))
	@echo "Token acquired"

publish: build login
	uv publish \
		--publish-url $(PUBLISH_URL) \
		--token $(TOKEN)

clean:
	rm -rf dist/
