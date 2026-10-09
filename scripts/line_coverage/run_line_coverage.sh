#!/bin/bash
cd /Users/tdyar/ws/iris-vector-graph
export IVG_TEST_CONTAINER=ivg-iris-enterprise IVG_PORT=31972
run() { # ns outfile listfile resultsfile
  docker exec ivg-iris-enterprise rm -f /tmp/cov.stop /tmp/$2
  (printf 'do ##class(IVG236Tmp.Cov).Run("%s","/tmp/cov.stop","/tmp/%s")\nhalt\n' "$(cat /tmp/all_routines.txt)" "$2" | docker exec -i ivg-iris-enterprise iris session IRIS -U $1 >/tmp/drv_$1.log 2>&1 &)
  sleep 10
  : > $4
  for f in $(cat $3); do
    r=$(.venv/bin/python -m pytest "$f" -q -p no:cacheprovider 2>&1 | tail -1)
    echo "$f :: $r" >> $4
  done
  docker exec ivg-iris-enterprise touch /tmp/cov.stop
  for i in $(seq 1 100); do docker exec ivg-iris-enterprise tail -1 /tmp/$2 2>/dev/null | grep -q DONE && break; sleep 3; done
  docker cp ivg-iris-enterprise:/tmp/$2 /tmp/$2
  sleep 5
}
run IVG236 final_236.out /tmp/f236.txt /tmp/final_236_results.txt
run IVGFHIR final_fhir.out /tmp/fhir_files.txt /tmp/final_fhir_results.txt
ls tests/integration/test_*.py tests/e2e/test_*.py > /tmp/all_files.txt
run USER final_user.out /tmp/all_files.txt /tmp/final_user_results.txt
echo ALLDONE > /tmp/final_done.txt
