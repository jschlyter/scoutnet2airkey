CLEANFILES=	*.json


all:

container:
	docker build --pull -t scoutnet2airkey .

test:
	uv run python scoutnet2airkey.py

lint:
	uv run ruff check

clean:
	rm -f $(CLEANFILES)
