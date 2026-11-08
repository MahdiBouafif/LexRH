# Checking the expected articles

Nothing in `retrieval_questions_v2.jsonl` is verified. For each case, print the article as it
stood on the case date and compare it with the question:

```
docker compose exec postgres psql -U lexrh -d lexrh -c \
 "SELECT num, valid_from, valid_to, left(text, 400) FROM documents
  WHERE num = 'L1221-19' AND valid_from <= '2023-01-01'
    AND (valid_to IS NULL OR valid_to > '2023-01-01') AND status <> 'ANNULE';"
```

(adjust user/database to your `.env`). Then, for each line:
1. The article answers the question on that date -> set `"verified": true`.
2. A different article answers it -> change `expected`, then verify.
3. You cannot tell, or the Code does not cover it -> delete the line.

Do not tune the system on cases that are still unverified.
