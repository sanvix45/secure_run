==> Cloning from https://github.com/sanvix45/secure_run
==> Checking out commit 8903a62b420054f9af9f656216f82f9c7697c949 in branch main
#1 [internal] load build definition from Dockerfile
#1 transferring dockerfile: 836B done
#1 DONE 0.0s
Dockerfile:5
--------------------
   3 |     FROM python:3.10-slim
   4 |     
   5 | >>> Install system dependencies and Google Chrome
   6 |     
   7 |     RUN apt-get update && apt-get install -y 
--------------------
error: failed to solve: dockerfile parse error on line 5: unknown instruction: Install
