import boto3
import datetime
import os

# todo -- some elements only allow you to list up to 10 or 100 items at a time; make iterators

EXCLUSION_LIST = []
DEBUG = True


def ecs_cpu_utilization(cluster_name, cw):
    response = cw.get_metric_statistics(
        Namespace='AWS/ECS',
        MetricName='CPUUtilization',
        Dimensions=[
            {
                'Name': 'ClusterName',
                'Value': cluster_name,
            },
        ],
        StartTime=datetime.datetime.utcnow() - datetime.timedelta(seconds=300),
        EndTime=datetime.datetime.utcnow(),
        Period=300,
        Statistics=['Average'],
    )

    return "{:.2f}".format(response['Datapoints'][0]['Average'])


def ecs_memory_utilization(cluster_name, cw):
    response = cw.get_metric_statistics(
        Namespace='AWS/ECS',
        MetricName='MemoryUtilization',
        Dimensions=[
            {
                'Name': 'ClusterName',
                'Value': cluster_name,
            },
        ],
        StartTime=datetime.datetime.utcnow() - datetime.timedelta(seconds=300),
        EndTime=datetime.datetime.utcnow(),
        Period=300,
        Statistics=['Average'],
    )

    return "{:.2f}".format(response['Datapoints'][0]['Average'])


def lambda_handler(event, context):
    cw = boto3.client('cloudwatch')
    ecs = boto3.client('ecs')
    asg = boto3.client('autoscaling')

    all_clusters = ecs.list_clusters()
    cluster_names = list()
    for clusterArn in all_clusters['clusterArns']:
        cluster_names.append(clusterArn.split('/')[1])

    cluster_names.sort()

    for clusterName in cluster_names:
        if clusterName in EXCLUSION_LIST:
            if DEBUG:
                print('Found cluster (%s) in exclusion list, skipping...' % clusterName)
            continue

        cluster_services = ecs.list_services(cluster=clusterName)  # 10 limit
        print('Getting services for cluster (%s)' % clusterName)
        cluster_services_descriptions = ecs.describe_services(cluster=clusterName,
                                                              services=cluster_services['serviceArns'])  # 10 limit
        largest_service = dict(name='', cpu=0, memory=0)
        for service in cluster_services_descriptions['services']:
            service_task_definition = ecs.describe_task_definition(taskDefinition=service['taskDefinition'])
            cpu = memory = 0
            for definition in service_task_definition['taskDefinition']['containerDefinitions']:
                cpu += definition['cpu']
                if 'memoryReservation' in definition:
                    memory += definition['memoryReservation']
                else:
                    memory += definition['memory']
                if cpu > largest_service['cpu'] or memory > largest_service['memory']:
                    largest_service = dict(name=service['serviceName'], cpu=cpu, memory=memory)
            if DEBUG:
                print('Service %s needs %s CPU and %s memory' % (service['serviceName'], cpu, memory))
        if DEBUG:
            print('The largest service on %s is %s requiring %s CPU Shares and %s Memory Shares'
                  % (clusterName, largest_service['name'], largest_service['cpu'], largest_service['memory']))
        cluster_container_instances_list = ecs.list_container_instances(cluster=clusterName, status='ACTIVE')  # 100

        cluster_container_instances = \
            ecs.describe_container_instances(cluster=clusterName,
                                             containerInstances=cluster_container_instances_list['containerInstanceArns'])

        can_support_largest_task = False
        largest_task_support_count = 0
        cluster_instance_count = 0
        consider_scaling = 0  # -1 to scale in, 0 to stay, 1 to scale out

        '''
        #  commented out to prevent perceived DoS attacks, we already make a lot of requests to the API
        print('The average CPU Utilization for Cluster ({}) is {}%'
              .format(clusterName, ecs_cpu_utilization(clusterName, cw)))
        print('The average Memory Utilization for Cluster ({}) is {}%'
              .format(clusterName, ecs_memory_utilization(clusterName, cw)))
        '''
        for cluster_instance in cluster_container_instances['containerInstances']:
            #  grab the ID of the instance to grab ASG info later
            cluster_random_instance_id = cluster_instance['ec2InstanceId']
            cluster_instance_count += 1
            remaining_resources = {resource['name']: resource for resource in cluster_instance['remainingResources']}
            remaining_cpu = int(remaining_resources['CPU']['integerValue'])
            remaining_ram = int(remaining_resources['MEMORY']['integerValue'])
            if DEBUG:
                print('Cluster instance (%s) has %s CPU Shares and %s RAM Shares remaining'
                      % (cluster_instance['ec2InstanceId'], remaining_cpu, remaining_ram))

            if remaining_cpu >= largest_service['cpu'] and remaining_ram >= largest_service['memory']:
                can_support_largest_task = True
                largest_task_support_count += 1

        # find out which ASG this cluster instance belongs to
        res = asg.describe_auto_scaling_instances(InstanceIds=[cluster_random_instance_id])
        asg_name = res['AutoScalingInstances'][0]['AutoScalingGroupName']
        # find out the min/max/desired size for the ASG
        res = asg.describe_auto_scaling_groups(AutoScalingGroupNames=[asg_name])
        asg_info = res['AutoScalingGroups'][0]
        cluster_min_size = asg_info['MinSize']
        cluster_max_size = asg_info['MaxSize']
        cluster_desired_size = asg_info['DesiredCapacity']

        if can_support_largest_task:
            print('Cluster (%s) has enough resources to support %s of the largest service (%s)'
                  % (clusterName, largest_task_support_count, largest_service['name']))
        else:
            consider_scaling = 1
            print('Cluster (%s) needs to scale to support the largest service (%s)'
                  % (clusterName, largest_service['name']))

        num_cluster_instances = len(cluster_container_instances_list['containerInstanceArns'])
        print('Cluster (%s) requires %s, desires %s, maxes at %s, has %s instances and can support %s largest tasks'
              % (clusterName, cluster_min_size, cluster_desired_size,
                 cluster_max_size, num_cluster_instances, largest_task_support_count))

        # Check that we don't already need to scale
        # Also check for too many hosts and too much capacity
        if consider_scaling != 1:
            if (cluster_desired_size + 1) > cluster_instance_count > cluster_min_size:
                print('Cluster (%s) currently has %s instances, with a minimum cluster size of %s' %
                      (clusterName, cluster_instance_count, cluster_min_size))
                if largest_task_support_count > 2:
                    consider_scaling = -1
                    print('Cluster (%s) can support %s of the largest tasks; consider scaling in'
                          % (clusterName, largest_task_support_count))

        print('The current scaling metric would be set to %s' % consider_scaling)

        cw.put_metric_data(Namespace='ECS',
                           MetricData=[{
                               'MetricName': 'NeedsToScaleOut',
                               'Dimensions': [{
                                   'Name': 'ClusterName',
                                   'Value': clusterName
                               }],
                               'Timestamp': datetime.datetime.utcnow(),
                               'Value': consider_scaling
                           }])
        print('Metric was sent to CloudWatch')

    return {}
