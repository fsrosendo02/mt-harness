#!/bin/bash
bugrev=3fe0caeada

run_test()
{
    cd src
        timeout 5 perl /experiment/gzip-run-tests.pl $1
    RESULT=$?
    
    cd ..
    return $RESULT
}
case $1 in
    p1) run_test 1 && exit 0 ;; 
    p2) run_test 4 && exit 0 ;; 
    n1) run_test 3 && exit 0 ;; 
esac
exit 1