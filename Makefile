run:
	python3 app/server.py

test:
	python3 -m unittest discover -s tests -v

check:
	python3 scripts/check_release.py
	python3 scripts/check_release_claims.py

claims:
	python3 scripts/check_release_claims.py --json

smoke:
	python3 scripts/smoke_e2e.py

verify-audit:
	python3 scripts/verify_audit.py $${DB_PATH:-data/project_xray.db}

recovery-evidence:
	python3 scripts/recovery_evidence.py $${DB_PATH:-data/project_xray.db} artifacts/recovery-drill.json

migrate-v3:
	python3 scripts/migrate_v2_to_v3.py $${DB_PATH:-data/project_xray.db}

backup:
	python3 scripts/recovery.py backup $${DB_PATH:-data/project_xray.db} backups/project_xray.db

preflight:
	python3 scripts/preflight_prod_env.py --output artifacts/prod-rehearsal/preflight.json

prod-rehearsal:
	python3 scripts/rehearse_production.py

package:
	./scripts/create_release.sh
