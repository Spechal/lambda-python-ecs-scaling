import boto3
import datetime

# todo -- some elements only allow you to list up to 10 items at a time; make iterators


def lambda_handler(event, context):
    cw = boto3.client('cloudwatch')
    ecs = boto3.client('ecs')

    allClusters = ecs.list_clusters()
    clusterNames = list()
    for clusterArn in allClusters['clusterArns']:
        clusterNames.append(clusterArn.split('/')[1])

    clusterNames.sort()

    for clusterName in clusterNames:
        clusterServices = ecs.list_services(cluster=clusterName)  # 10 limit
        clusterServicesDescriptions = ecs.describe_services(cluster=clusterName,
                                                            services=clusterServices['serviceArns'])  # 10 limit
        largestService = dict(name='', cpu=0, memory=0)
        for service in clusterServicesDescriptions['services']:
            serviceTaskDefinition = ecs.describe_task_definition(taskDefinition=service['taskDefinition'])
            cpu = memory = 0
            for definition in serviceTaskDefinition['taskDefinition']['containerDefinitions']:
                cpu += definition['cpu']
                memory += definition['memory']
                if cpu > largestService['cpu'] or memory > largestService['memory']:
                    largestService = dict(name=service['serviceName'], cpu=cpu, memory=memory)

            print('Service %s needs %s CPU and %s memory' % (service['serviceName'], cpu, memory))

        print('The largest service on %s is %s requiring %s CPU Shares and %s Memory Shares'
              % (clusterName, largestService['name'], largestService['cpu'], largestService['memory']))

        clusterContainerInstancesList = ecs.list_container_instances(cluster=clusterName, status='ACTIVE')
        print('The cluster (%s) has %s EC2 instances associated'
              % (clusterName, len(clusterContainerInstancesList['containerInstanceArns'])))

        clusterContainerInstances = \
            ecs.describe_container_instances(cluster=clusterName,
                                             containerInstances=clusterContainerInstancesList['containerInstanceArns'])

        for clusterInstance in clusterContainerInstances['containerInstances']:
            canSupportLargestTask = False
            remainingResources = {resource['name']: resource for resource in clusterInstance['remainingResources']}
            remainingCPU = int(remainingResources['CPU']['integerValue'])
            remainingRAM = int(remainingResources['MEMORY']['integerValue'])
            print('The cluster instance (%s) has %s CPU Shares left and %s RAM Shares'
                  % (clusterInstance['ec2InstanceId'], remainingCPU, remainingRAM))

            if remainingCPU >= largestService['cpu'] and remainingRAM >= largestService['memory']:
                canSupportLargestTask = True

        if canSupportLargestTask:
            print('The cluster (%s) has enough resources to support the largest service (%s)'
                  % (clusterName, largestService['name']))
        else:
            print('The cluster (%s) needs to scale to support the largest service (%s)'
                  % (clusterName, largestService['name']))

        cw.put_metric_data(Namespace='ECS',
                           MetricData=[{
                               'MetricName': 'NeedsToScaleOut',
                               'Dimensions': [{
                                   'Name': 'ClusterName',
                                   'Value': clusterName
                               }],
                               'Timestamp': datetime.datetime.utcnow(),
                               'Value': (1 if not canSupportLargestTask else 0)
                           }])
        print('Metric was sent to CloudWatch')

    return {}
