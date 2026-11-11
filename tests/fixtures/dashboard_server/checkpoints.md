RUN=2026-01-10T10:00:00+01:00
- 10:00 start aktu III (HEAD abc1234)
- 10:25 checkpoint 1: zrobione S1 (+review PASS), S2a; w toku: S2b (test-writer), S3a (implementer), szczebel 3 S1 na migawce, baseline; ryzyko: równoległe buildy
- 10:40 restart sesji: przerwane S2b i S3a; wznowione wszystkie
  kontynuacja linii checkpointu
