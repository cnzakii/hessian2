.DEFAULT_GOAL := check

.PHONY: check lint test-rust test-python

check: lint test-rust test-python

lint:
	cargo fmt --all -- --check
	cargo clippy --workspace --all-targets --all-features --locked -- -D warnings
	uv run --locked ruff check .
	uv run --locked ruff format --check .
	uv run --locked ty check

test-rust:
	cargo test --workspace --all-features --locked

test-python:
	uv --directory crates/hessian2-python run --locked maturin develop --release
	uv run --locked pytest

# Java interoperability: Caucho's reference implementation and hessian-lite.
# Set JAVA_HOME to a JDK 17+; tests that need it skip when the harness is absent.
JAVA_BIN := $(if $(JAVA_HOME),$(JAVA_HOME)/bin/,)
JAVA_DIR := interop/java
MAVEN := https://repo1.maven.org/maven2

.PHONY: interop-java fixtures-java

interop-java:
	mkdir -p $(JAVA_DIR)/lib
	cd $(JAVA_DIR)/lib && for jar in \
	    com/caucho/hessian/4.0.66/hessian-4.0.66.jar \
	    org/apache/dubbo/hessian-lite/4.0.5/hessian-lite-4.0.5.jar \
	    javax/servlet/javax.servlet-api/3.1.0/javax.servlet-api-3.1.0.jar; do \
	  [ -f "$$(basename $$jar)" ] || curl -fsSLO "$(MAVEN)/$$jar"; \
	done && shasum -a 256 -c ../jars.sha256
	rm -rf $(JAVA_DIR)/build
	$(JAVA_BIN)javac -encoding UTF-8 -nowarn -d $(JAVA_DIR)/build \
	  -cp "$(JAVA_DIR)/lib/*" $(JAVA_DIR)/src/interop/Interop.java

fixtures-java: interop-java
	$(JAVA_BIN)java -cp "$(JAVA_DIR)/build:$(JAVA_DIR)/lib/*" interop.Interop write \
	  crates/hessian2-python/tests/fixtures/java.json

.PHONY: fixtures-go

# Go interoperability: dubbo-go-hessian2, pinned in interop/go/go.mod.
fixtures-go:
	cd interop/go && go run . write ../../crates/hessian2-python/tests/fixtures/go.json
