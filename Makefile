CLEANFILES=	*.json


all:

container:
	docker build --pull -t scoutnet2airkey .

test:
	poetry run scoutnet2airkey.py

lint:
	poetry run pylama *.py

clean:
	rm -f $(CLEANFILES)
