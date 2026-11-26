PY ?= python

.PHONY: setup validate smoke radio film assemble costs
setup:    ; $(PY) -m pip install -r requirements.txt
validate: ; $(PY) run.py validate
smoke:    ; $(PY) run.py smoke
radio:    ; $(PY) run.py radio
film:     ; $(PY) run.py film
assemble: ; $(PY) run.py assemble
costs:    ; $(PY) run.py costs
