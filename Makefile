CLEANFILES=	*.json


all:

container:
	docker build --pull -t scoutnet2airkey .

test:
	poetry run scoutnet2airkey.py

lint:
	poetry run ruff check

clean:
	rm -f $(CLEANFILES)
