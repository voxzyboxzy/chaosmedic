.PHONY: demo lab test eval ci

demo:
	./start.sh demo

lab:
	./start.sh lab

test:
	PYTHONPATH=backend:. python3 -m pytest
	cd frontend && npm test

eval:
	PYTHONPATH=backend:. python3 -m chaosmedic.eval --write

ci: test
	PYTHONPATH=backend:. python3 -m chaosmedic.eval --check --min-accuracy 0.8
	cd frontend && npm run build
