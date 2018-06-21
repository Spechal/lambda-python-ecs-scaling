# ECS Host Based Scaling Metrics

This lambda function will inspect all ECS clusters and send a custom CloudWatch metric depending on various conditionals.

---

As written, the Lambda function will send a metric of -1 to scale a cluster in based on the following:

* The cluster must be able to support 2 or more of the largest task definitions for a service
* The number of hosts currently registered to the cluster must be 3 or more

As written, the Lambda function will send a metric of 0 to leave a cluster as is based on the following:

* It must be able to support 1 or more of the largest service's task definition

As written, the Lambda function will send a metric of 1 to scale a cluster out based on the following:

* It must not be able to support the largest service's task definition

---

To test the function, you can use `python-lambda-local`.  Beware that this WILL send the metrics to CloudWatch as currently written.

* `pip3 install python-lambda-local`
* `python-lambda-local -t 300 -f lambda_handler lambda_function.py event.json`
