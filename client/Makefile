SWAGGER_JSON=	swagger.json
SWAGGER_URL=	https://integration.api.airkey.evva.com/docs/swagger.json

CLEANFILES=	setup.py $(SWAGGER_JSON)


all: wheel

codegen: setup.py

setup.py: $(SWAGGER_JSON)
	swagger-codegen generate -l python -i $(SWAGGER_JSON) -o . -c codegen-config.json

wheel: setup.py
	python3 setup.py bdist_wheel

$(SWAGGER_JSON):
	curl -o $@ $(SWAGGER_URL)

clean:
	rm -f $(CLEANFILES)
