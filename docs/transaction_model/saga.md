Version: v2.6

## SEATA Saga Pattern

## Overview

The Saga pattern is a long transaction solution provided by SEATA. In the Saga pattern, each participant in the business process submits a local transaction. If any participant fails, the Saga pattern compensates the previously successful participants. Both the forward service in phase one and the compensation service in phase two are implemented by business development.

![Saga Mode Overview](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAb0AAAG8CAMAAAB9rIvTAAAIwnRFWHRteGZpbGUAJTNDbXhmaWxlJTIwbW9kaWZpZWQlM0QlMjIyMDIwLTAxLTA3VDEyJTNBMjMlM0E0NS41MjlaJTIyJTIwaG9zdCUzRCUyMkVsZWN0cm9uJTIyJTIwYWdlbnQlM0QlMjJNb3ppbGxhJTJGNS4wJTIwKE1hY2ludG9zaCUzQiUyMEludGVsJTIwTWFjJTIwT1MlMjBYJTIwMTBfMTFfNiklMjBBcHBsZVdlYktpdCUyRjUzNy4zNiUyMChLSFRNTCUyQyUyMGxpa2UlMjBHZWNrbyklMjBkcmF3LmlvJTJGMTIuMy4yJTIwQ2hyb21lJTJGNzguMC4zOTA0LjExMyUyMEVsZWN0cm9uJTJGNy4xLjIlMjBTYWZhcmklMkY1MzcuMzYlMjIlMjBldGFnJTNEJTIyT0hZZnpnOGdHY3N4V2tkek1EMEolMjIlMjB2ZXJzaW9uJTNEJTIyMTIuMy4yJTIyJTIwdHlwZSUzRCUyMmRldmljZSUyMiUyMHBhZ2VzJTNEJTIyMSUyMiUzRSUzQ2RpYWdyYW0lMjBpZCUzRCUyMjhWczRpZ09wTWhPZDBTQzJNc2dCJTIyJTIwbmFtZSUzRCUyMlBhZ2UtMSUyMiUzRTVWcGRjNXM0RlAwMWZzd09RbnclMkJPbzZUN1d6YTZVNDYyJTJCMmpBckt0TGtZZUljZDJmdjBLSXd4STJCQnFqSk8lMkJlTkNWRU9qYzQzdnVGUnJCeVhMN3dOQnE4Wm1HT0JxWlJyZ2R3YnVSYWZxV0kzNVR3eTR6T0o2UkdlYU1oSmtKRklZbjhvcWxNUiUyQjJKaUZPS2dNNXBSRW5xNm94b0hHTUExNnhJY2JvcGpwc1JxUHFVMWRvampYRFU0QWkzZnFkaEh5UldUM1RMZXglMkZZakpmNUU4R2pwJTJGMUxGRSUyQldLNGtXYUNRYmtvbU9CM0JDYU9VWjFmTDdRUkhLWFk1THRsOTkwZDZEeSUyRkdjTXpiM1BBUSUyRnZWUHRQbk9qT25OZW1xdnZkMXVQTDR4WVRiTkM0cldjc1h5YmZrdWg0RFJkUnppZEJaakJHODNDOEx4MHdvRmFlOUclMkJGellGbndaaVJZUWx5RktGdnV4YVdOR1l5NWRDanpSMWw5WnJ1SUZNNDYzSlpOY3dnT21TOHpaVGd5UnZkQ1FieXo1QktDRWQxTjRCempTdGloNUJocVdaSVZreFB3d2R3R2F1SkM0dlFGRDRMd3pESDJnUUFqYVFXZ1pmbDhRYWdoJTJCQXljd0JNMFlxckROU0JSTmFFVFpmaTRZMnRnTExXRlBPS1AlMkY0VktQWno1RHh6a1AwQ0NQWWpuU2RnMVp6UnFrYyUyRlRQRDdUWHpGVWNpZ2dvbTVUeEJaM1RHRVhUd25wYlpYTXg1cEhTbGNUJTJGSiUyQlo4SngyQTFweWU5czVSckJPNlpnRSUyQkZjQ2tKaUEyeDd5WlllbmFUbnFPNFFoeDhsS04lMkZtZDNnNm56JTJGVlRNZUs5OFAlMkZCNE1MN3I4dllOZmtDZ1laNTFEQWEwcFFNZGYwQ2dMWGRvUnR2TkFWeGttcXYwY2hiaDdUak5nUVVXT0E3bDVWMFFvU1Fod1dtMDhaYndmOU1BJTJGNGN0V3olMkZreVBUNmJpdGolMkY3NnhrdzNWUXdoN3M2RE9RMDdnNGVmWkw4WDl2QlpvaXZ0bXk3aGY4cWRkNDg3YzFsb2U1Qk8lMkJVaUpXVm1TdnB2SyUyRmhRcE5zblhMdThySnZEcVJxMFJhVDVrb0EwYWJhRSUyQjV3N0s3czdCRnlqc1VDd1dWMks1MFU5cjhVZTRyYnR1M2htQnYyNndGWGhWN2dhZVF6dXpJWHVDcThuVlo5cm9YWW05SEpsNTc3SVV0Mld0ZEZYdE5xRWk1ZFNiMldxNXpVZmJxSmR5azMySjU1Z1U0cUNYVHMyZGJ0bkdtblIzejZvcGxRMGU2M3pKdEdLU0hMOU9BdmdFMDZiZE9Hd2JwNGVzMDBHTER0NlJ6TVkyeHVpUFpJbDhESmQwcVZLd3BYNnRvWkNHWlIxU3l1MzYxclIyOHE5WXZzNnQlMkJtVW9hQiUyRnpMWmw5QTN5czRKd1U3MHFrTGRidFRzRzBCa0V2UXRYRFFha2pjVzNQUVZ5WUNGJTJCWmdpMjJVajg3QnRtbDhMczdYd2tFMSUyRlZiRFYlMkJzOUZMT2hJT2liZzNvWmVqJTJGJTJCOVBpa0VUR3ZSUU1VUlhUTm0zT2ZGV1pFdkIxbXhVMWZDNU5HMXhWTkNDYzBOaVdsR09Vb2JZ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)

Theoretical Basis: Paper by Hector & Kenneth titled "Sagas" (1987)

### Applicable Scenarios:

- Long and numerous business processes.
- Participants include other companies or legacy system services that cannot provide the three interfaces required by the TCC pattern.

### Advantages:

- One-phase commits local transaction, no locks, high performance.
- Event-driven architecture, participants can execute asynchronously, high throughput.
- Compensation service is easy to implement.

### Disadvantages:

- Does not guarantee isolation (see later documents for solutions).

### Implementation of Saga:

#### Saga implementation based on state machine engine:

SEATA's current Saga pattern implementation is based on a state machine engine, which works as follows:

1. Define the service call process through a state diagram and generate a JSON state language definition file.
2. A node in the state diagram can be a service call, and each node can configure its compensation node.
3. The state diagram JSON is driven by the state machine engine. When an exception occurs, the engine reverses the execution of the compensation nodes for the successful nodes to roll back the transaction.

> Note: Whether to compensate in case of an exception can also be decided by the user.

4. It can implement service orchestration needs, supporting features such as single choice, concurrency, sub-processes, parameter conversion, parameter mapping, service execution status judgment, and exception capture.

Example state diagram:

![Example State Diagram](https://seata.apache.org/assets/images/demo_statelang-90f1fc01bfaf3a795c3b3357e1046f16.png)

## Quick Start

### Demo Introduction

Using the Saga pattern under microservices built with Dubbo to demonstrate the submission and rollback of distributed transactions;

The business process diagram is shown below:

![Demo Business Process Diagram](https://seata.apache.org/assets/images/demo_business_process-d7e667de4ce267e36b6851a1e820bc5b.png)

First, download the seata-samples project: [https://github.com/apache/incubator-seata-samples.git](https://github.com/apache/incubator-seata-samples.git)

> Note: The SEATA version needs to be 0.9.0 or above.

In the dubbo-saga-sample, a distributed transaction will involve 2 Saga transaction participants: [InventoryAction](https://github.com/apache/incubator-seata-samples/blob/master/saga/dubbo-saga-sample/src/main/java/io/seata/samples/saga/action/InventoryAction.java) and [BalanceAction](https://github.com/apache/incubator-seata-samples/blob/master/saga/dubbo-saga-sample/src/main/java/io/seata/samples/saga/action/BalanceAction.java). If the distributed transaction commits, both participants commit; if it rolls back, both participants roll back.

These two Saga participants are Dubbo services. Both participants have a reduce method, which represents inventory reduction or balance reduction, and a compensateReduce method for compensating the reduction operation.

- InventoryAction interface definition:
```java
public interface InventoryAction {

    /**
     * reduce
     * @param businessKey
     * @param amount
     * @param params
     * @return
     */
    boolean reduce(String businessKey, BigDecimal amount, Map<String, Object> params);

    /**
     * compensateReduce
     * @param businessKey
     * @param params
     * @return
     */
    boolean compensateReduce(String businessKey, Map<String, Object> params);
}
```
- The scenario defined in state language is the following JSON: src/main/resources/statelang/reduce\_inventory\_and\_balance.json
```json
{
    "Name": "reduceInventoryAndBalance",
    "Comment": "reduce inventory then reduce balance in a transaction",
    "StartState": "ReduceInventory",
    "Version": "0.0.1",
    "States": {
        "ReduceInventory": {
            "Type": "ServiceTask",
            "ServiceName": "inventoryAction",
            "ServiceMethod": "reduce",
            "CompensateState": "CompensateReduceInventory",
            "Next": "ChoiceState",
            "Input": [
                "$.[businessKey]",
                "$.[count]"
            ],
            "Output": {
                "reduceInventoryResult": "$.#root"
            },
            "Status": {
                "#root == true": "SU",
                "#root == false": "FA",
                "$Exception{java.lang.Throwable}": "UN"
            }
        },
        "ChoiceState":{
            "Type": "Choice",
            "Choices":[
                {
                    "Expression":"[reduceInventoryResult] == true",
                    "Next":"ReduceBalance"
                }
            ],
            "Default":"Fail"
        },
        "ReduceBalance": {
            "Type": "ServiceTask",
            "ServiceName": "balanceAction",
            "ServiceMethod": "reduce",
            "CompensateState": "CompensateReduceBalance",
            "Input": [
                "$.[businessKey]",
                "$.[amount]",
                {
                    "throwException" : "$.[mockReduceBalanceFail]"
                }
            ],
            "Output": {
                "compensateReduceBalanceResult": "$.#root"
            },
            "Status": {
                "#root == true": "SU",
                "#root == false": "FA",
                "$Exception{java.lang.Throwable}": "UN"
            },
            "Catch": [
                {
                    "Exceptions": [
                        "java.lang.Throwable"
                    ],
                    "Next": "CompensationTrigger"
                }
            ],
            "Next": "Succeed"
        },
        "CompensateReduceInventory": {
            "Type": "ServiceTask",
            "ServiceName": "inventoryAction",
            "ServiceMethod": "compensateReduce",
            "Input": [
                "$.[businessKey]"
            ]
        },
        "CompensateReduceBalance": {
            "Type": "ServiceTask",
            "ServiceName": "balanceAction",
            "ServiceMethod": "compensateReduce",
            "Input": [
                "$.[businessKey]"
            ]
        },
        "CompensationTrigger": {
            "Type": "CompensationTrigger",
            "Next": "Fail"
        },
        "Succeed": {
            "Type":"Succeed"
        },
        "Fail": {
            "Type":"Fail",
            "ErrorCode": "PURCHASE_FAILED",
            "Message": "purchase failed"
        }
    }
}
```

The state diagram represented by this JSON:

![State Diagram Represented by JSON](https://seata.apache.org/assets/images/demo_statelang-90f1fc01bfaf3a795c3b3357e1046f16.png)

The provided text introduces the concept of "State Machine" and its attributes in the context of Seata's Saga pattern, which is somewhat influenced by [AWS Step Functions](https://docs.aws.amazon.com/zh_cn/step-functions/latest/dg/tutorial-creating-lambda-state-machine.html). Here's the translation:

#### Introduction to "State Machine" Properties:

- **Name**: Represents the unique name of the state machine.
- **Comment**: A description of the state machine.
- **Version**: The version of the state machine definition.
- **StartState**: The first "state" to run when starting.
- **States**: A list of states, structured as a map where the key is the unique name of the "state" within the state machine.
- **IsRetryPersistModeUpdate**: Whether the log is updated based on the last failed log during forward retry.
- **IsCompensatePersistModeUpdate**: Whether the log is updated based on the last compensation log during backward compensation.

#### Introduction to "State" Properties:

- **Type**: The type of "state", for example:
	- **ServiceTask**: Executes a service call task.
		- **Choice**: Single condition selection routing.
		- **CompensationTrigger**: Triggers the compensation process.
		- **Succeed**: The state machine ends normally.
		- **Fail**: The state machine ends abnormally.
		- **SubStateMachine**: Calls a sub-state machine.
		- **CompensateSubMachine**: Used to compensate a sub-state machine.
- **ServiceName**: The name of the service, usually the beanId of the service.
- **ServiceMethod**: The name of the service method.
- **CompensateState**: The compensation "state" of that "state".
- **Loop**: Indicates whether the transaction node is a loop transaction, i.e., the framework itself iterates over the collection elements based on the configuration of the loop attributes and executes the transaction node in a loop.
- **Input**: The list of input parameters for calling the service, which is an array corresponding to the parameter list of the service method. `$` indicates using an expression to take parameters from the state machine context, expressed using [SpringEL](https://docs.spring.io/spring/docs/4.3.10.RELEASE/spring-framework-reference/html/expressions.html). If it is a constant, write the value directly.
- **Output**: Maps the returned parameters of the service to the state machine context, structured as a map. The key is the key when put into the state machine context (which is also a map), and the value with `$` indicates a SpringEL expression to take values from the service's returned parameters. `#root` represents the entire return parameter of the service.
- **Status**: The mapping of service execution status. The framework defines three statuses: SU (Success), FA (Failure), and UN (Unknown). We need to map the execution status of the service to these three statuses to help the framework judge the consistency of the entire transaction. It's structured as a map where the key is a conditional expression, generally judging from the service's return value or thrown exception. Expressions starting with `$Exception{` indicate judging the type of exception. The value is the mapped execution status when this conditional expression holds.
- **Catch**: Routing after catching an exception.
- **Next**: The next "state" to execute after the service completes.
- **Choices**: In the Choice type "state", it's a list of optional branches. The Expression in the branches is a SpringEL expression, and Next is the next "state" to execute when the expression holds.
- **ErrorCode**: The error code of the Fail type "state".
- **Message**: The error message of the Fail type "state".

For a more detailed explanation of state language, please see the [State language reference](#state-language-reference) section.

For more detailed examples of state language usage, see [https://github.com/apache/incubator-seata/tree/develop/test/src/test/java/io/seata/saga/engine](https://github.com/apache/incubator-seata/tree/develop/test/src/test/java/io/seata/saga/engine).

### Demo Running Guide

#### Step 1: Start the SEATA Server

Run [SeataServerStarter](https://github.com/apache/incubator-seata-samples/blob/master/saga/sofarpc-saga-sample/src/test/java/io/seata/samples/saga/SeataServerStarter.java) to start the Seata Server.

#### Step 2: Start the Dubbo Provider Demo

Run [DubboSagaProviderStarter](https://github.com/apache/incubator-seata-samples/blob/master/saga/dubbo-saga-sample/src/test/java/io/seata/samples/saga/starter/DubboSagaProviderStarter.java) to start the Dubbo provider.

#### Step 3: Start the Saga Demo

Run [DubboSagaTransactionStarter](https://github.com/apache/incubator-seata-samples/blob/master/saga/dubbo-saga-sample/src/main/java/io/seata/samples/saga/starter/DubboSagaTransactionStarter.java) to start the demo project.

> The demo uses the H2 in-memory database. For production, it is recommended to use the same type of database as your business. Currently, it supports Oracle, MySQL, and DB2. The SQL scripts for table creation can be found at [https://github.com/apache/incubator-seata/tree/develop/saga/seata-saga-engine-store/src/main/resources/sql](https://github.com/apache/incubator-seata/tree/develop/saga/seata-saga-engine-store/src/main/resources/sql).

> The demo also includes examples of calling local services and SOFA RPC services.

## State Machine Designer

[Try it online](https://seata.apache.org/saga-designer/)

Seata Saga provides a visual state machine designer for user convenience. For code and running guide, please refer to: [https://github.com/apache/incubator-seata/tree/refactor\_designer/saga/seata-saga-statemachine-designer](https://github.com/apache/incubator-seata/tree/refactor_designer/saga/seata-saga-statemachine-designer)

Screenshot of the state machine designer: ![State Machine Designer](https://seata.apache.org/assets/images/seata-saga-statemachine-designer-4d721b255c7c92189f04178dd7489e57.png)

## Best Practices

### Practical Experience in Designing Saga Services

#### Allowing Empty Compensation

- Empty Compensation: The compensation service is executed even though the original service was not.
- Reasons for Occurrence:
	- The original service times out (packet loss).
		- The Saga transaction triggers a rollback.
		- The compensation request is received before the original service request.

Therefore, in service design, allow empty compensation, i.e., return successful compensation and record the original business key when no compensable business key is found.

#### Preventing Hanging Control

- Hanging: The compensation service executes before the original service.
- Reasons for Occurrence:
	- The original service times out (congestion).
		- Saga transaction rollback is triggered.
		- The congested original service arrives later.

So, it is necessary to check whether the current business key already exists in the recorded keys of empty compensation. If it exists, refuse the execution of the service.

#### Idempotence Control

- Both the original and compensation services need to ensure idempotence. Due to potential network timeouts, retry strategies can be set. When retries occur, idempotence control should be used to prevent duplicate updates of business data.

### Dealing with Lack of Isolation

- Since Saga transactions do not guarantee isolation, extreme situations may arise where rollback operations cannot be completed due to dirty writes. For example, in a distributed transaction, first, user A is credited, and then user B’s balance is reduced. If user A spends the balance before the transaction is committed, and the transaction needs to be rolled back, compensation is not possible. This is a typical problem caused by lack of isolation. Common approaches in practice are:
	- When designing business processes, follow the principle of “prefer overpayment to underpayment.” Overpayment means the customer has less money and the institution has more, which can be refunded based on the institution's credibility. In contrast, underpayment means the missing money might not be recoverable. Therefore, the business process design should always deduct money first.
		- Some business scenarios may allow the business to ultimately succeed. If it is impossible to roll back, the process can continue retrying to complete subsequent steps. Therefore, in addition to providing "rollback" capabilities, the state machine engine also needs to offer "forward" capabilities to recover the context and continue execution, allowing the business to ultimately succeed and achieve final consistency.

### Performance Optimization

- Configuring the client parameter `client.rm.report.success.enable=false` improves performance by not reporting the status of a successfully executed branch transaction to the server.

> When the status of a previous branch transaction has not yet been reported, and the next branch transaction has already been registered, it can be assumed that the previous one was actually successful.

## API referance

#### StateMachineEngine API

```java
public interface StateMachineEngine {

    /**
     * start a state machine instance
     * @param stateMachineName
     * @param tenantId
     * @param startParams
     * @return
     * @throws EngineExecutionException
     */
    StateMachineInstance start(String stateMachineName, String tenantId, Map<String, Object> startParams) throws EngineExecutionException;

    /**
     * start a state machine instance with businessKey
     * @param stateMachineName
     * @param tenantId
     * @param businessKey
     * @param startParams
     * @return
     * @throws EngineExecutionException
     */
    StateMachineInstance startWithBusinessKey(String stateMachineName, String tenantId, String businessKey, Map<String, Object> startParams) throws EngineExecutionException;

    /**
     * start a state machine instance asynchronously
     * @param stateMachineName
     * @param tenantId
     * @param startParams
     * @param callback
     * @return
     * @throws EngineExecutionException
     */
    StateMachineInstance startAsync(String stateMachineName, String tenantId, Map<String, Object> startParams, AsyncCallback callback) throws EngineExecutionException;

    /**
     * start a state machine instance asynchronously with businessKey
     * @param stateMachineName
     * @param tenantId
     * @param businessKey
     * @param startParams
     * @param callback
     * @return
     * @throws EngineExecutionException
     */
    StateMachineInstance startWithBusinessKeyAsync(String stateMachineName, String tenantId, String businessKey, Map<String, Object> startParams, AsyncCallback callback) throws EngineExecutionException;

    /**
     * forward restart a failed state machine instance
     * @param stateMachineInstId
     * @param replaceParams
     * @return
     * @throws ForwardInvalidException
     */
    StateMachineInstance forward(String stateMachineInstId, Map<String, Object> replaceParams) throws ForwardInvalidException;

    /**
     * forward restart a failed state machine instance asynchronously
     * @param stateMachineInstId
     * @param replaceParams
     * @param callback
     * @return
     * @throws ForwardInvalidException
     */
    StateMachineInstance forwardAsync(String stateMachineInstId, Map<String, Object> replaceParams, AsyncCallback callback) throws ForwardInvalidException;

    /**
     * compensate a state machine instance
     * @param stateMachineInstId
     * @param replaceParams
     * @return
     * @throws EngineExecutionException
     */
    StateMachineInstance compensate(String stateMachineInstId, Map<String, Object> replaceParams) throws EngineExecutionException;

    /**
     * compensate a state machine instance asynchronously
     * @param stateMachineInstId
     * @param replaceParams
     * @param callback
     * @return
     * @throws EngineExecutionException
     */
    StateMachineInstance compensateAsync(String stateMachineInstId, Map<String, Object> replaceParams, AsyncCallback callback) throws EngineExecutionException;

    /**
     * skip current failed state instance and forward restart state machine instance
     * @param stateMachineInstId
     * @return
     * @throws EngineExecutionException
     */
    StateMachineInstance skipAndForward(String stateMachineInstId) throws EngineExecutionException;

    /**
     * skip current failed state instance and forward restart state machine instance asynchronously
     * @param stateMachineInstId
     * @param callback
     * @return
     * @throws EngineExecutionException
     */
    StateMachineInstance skipAndForwardAsync(String stateMachineInstId, AsyncCallback callback) throws EngineExecutionException;

    /**
     * get state machine configurations
     * @return
     */
    StateMachineConfig getStateMachineConfig();
}
```

#### StateMachine Execution Instance API:

```java
StateLogRepository stateLogRepository = stateMachineEngine.getStateMachineConfig().getStateLogRepository();
StateMachineInstance stateMachineInstance = stateLogRepository.getStateMachineInstanceByBusinessKey(businessKey, tenantId);

/**
 * State Log Repository
 *
 * @author lorne.cl
 */
public interface StateLogRepository {

    /**
     * Get state machine instance
     *
     * @param stateMachineInstanceId
     * @return
     */
    StateMachineInstance getStateMachineInstance(String stateMachineInstanceId);

    /**
     * Get state machine instance by businessKey
     *
     * @param businessKey
     * @param tenantId
     * @return
     */
    StateMachineInstance getStateMachineInstanceByBusinessKey(String businessKey, String tenantId);

    /**
     * Query the list of state machine instances by parent id
     *
     * @param parentId
     * @return
     */
    List<StateMachineInstance> queryStateMachineInstanceByParentId(String parentId);

    /**
     * Get state instance
     *
     * @param stateInstanceId
     * @param machineInstId
     * @return
     */
    StateInstance getStateInstance(String stateInstanceId, String machineInstId);

    /**
     * Get a list of state instances by state machine instance id
     *
     * @param stateMachineInstanceId
     * @return
     */
    List<StateInstance> queryStateInstanceListByMachineInstanceId(String stateMachineInstanceId);
}
```

#### StateMachine Definition API:

```java
StateMachineRepository stateMachineRepository = stateMachineEngine.getStateMachineConfig().getStateMachineRepository();
StateMachine stateMachine = stateMachineRepository.getStateMachine(stateMachineName, tenantId);

/**
 * StateMachineRepository
 *
 * @author lorne.cl
 */
public interface StateMachineRepository {

    /**
     * Gets get state machine by id.
     *
     * @param stateMachineId the state machine id
     * @return the get state machine by id
     */
    StateMachine getStateMachineById(String stateMachineId);

    /**
     * Gets get state machine.
     *
     * @param stateMachineName the state machine name
     * @param tenantId         the tenant id
     * @return the get state machine
     */
    StateMachine getStateMachine(String stateMachineName, String tenantId);

    /**
     * Gets get state machine.
     *
     * @param stateMachineName the state machine name
     * @param tenantId         the tenant id
     * @param version          the version
     * @return the get state machine
     */
    StateMachine getStateMachine(String stateMachineName, String tenantId, String version);

    /**
     * Register the state machine to the repository (if the same version already exists, return the existing version)
     *
     * @param stateMachine
     */
    StateMachine registryStateMachine(StateMachine stateMachine);

    /**
     * registry by resources
     *
     * @param resources
     * @param tenantId
     */
    void registryByResources(Resource[] resources, String tenantId) throws IOException;
}
```

## Config Reference

#### Configuring a StateMachineEngine in a Spring Bean Configuration File

```xml
<bean id="dataSource" class="...">
...
<bean>
<bean id="stateMachineEngine" class="io.seata.saga.engine.impl.ProcessCtrlStateMachineEngine">
        <property name="stateMachineConfig" ref="dbStateMachineConfig"></property>
</bean>
<bean id="dbStateMachineConfig" class="io.seata.saga.engine.config.DbStateMachineConfig">
    <property name="dataSource" ref="dataSource" />
    <property name="resources" value="statelang/*.json" />
    <property name="enableAsync" value="true" />
    <!-- Thread pool used for event-driven execution. If all state machines execute synchronously and there are no loop tasks, it may not be necessary. -->
    <property name="threadPoolExecutor" ref="threadExecutor" />
    <property name="applicationId" value="saga_sample" />
    <property name="txServiceGroup" value="my_test_tx_group" />
    <property name="sagaBranchRegisterEnable" value="false" />
    <property name="sagaJsonParser" value="fastjson" />
    <property name="sagaRetryPersistModeUpdate" value="false" />
    <property name="sagaCompensatePersistModeUpdate" value="false" />
</bean>
<bean id="threadExecutor"
        class="org.springframework.scheduling.concurrent.ThreadPoolExecutorFactoryBean">
    <property name="threadNamePrefix" value="SAGA_ASYNC_EXE_" />
    <property name="corePoolSize" value="1" />
    <property name="maxPoolSize" value="20" />
</bean>

<!-- Seata Server needs this Holder to get the stateMachineEngine instance for transaction recovery -->
<bean class="io.seata.saga.rm.StateMachineEngineHolder">
    <property name="stateMachineEngine" ref="stateMachineEngine"/>
</bean>
```

## State Language Reference

### List of "State Machine" Properties

```json
{
    "Name": "reduceInventoryAndBalance",
    "Comment": "reduce inventory then reduce balance in a transaction",
    "StartState": "ReduceInventory",
    "Version": "0.0.1",
    "States": {
    },
    "IsRetryPersistModeUpdate": false,
    "IsCompensatePersistModeUpdate": false
}
```
- **Name**: Represents the name of the state machine, which must be unique.
- **Comment**: A description of the state machine.
- **Version**: The version of the state machine definition.
- **StartState**: The first "state" to be executed at startup.
- **States**: A list of states, structured as a map where the key is the unique name of the "state" within the state machine, and the value is a map representing the properties of the "state".
- **IsRetryPersistModeUpdate**: Whether the log is updated based on the last failed log during a forward retry. By default, this is false, meaning a new retry log is added (this has a higher priority than the global stateMachineConfig configuration property).
- **IsCompensatePersistModeUpdate**: Whether the log is updated based on the last compensation log during a backward compensation. By default, this is false, meaning a new compensation log is added (this has a higher priority than the global stateMachineConfig configuration property).

### Property List of All States

#### ServiceTask:

```json
"States": {
    ...
    "ReduceBalance": {
        "Type": "ServiceTask",
        "ServiceName": "balanceAction",
        "ServiceMethod": "reduce",
        "CompensateState": "CompensateReduceBalance",
        "IsForUpdate": true,
        "IsPersist": true,
        "IsAsync": false,
        "IsRetryPersistModeUpdate": false,
        "IsCompensatePersistModeUpdate": false,
        "Loop": {
            "Parallel": 3,
            "Collection": "$.[collection]",
            "ElementVariableName": "element",
            "ElementIndexName": "loopCounter",
            "CompletionCondition": "[nrOfCompletedInstances] / [nrOfInstances] >= 0.6"
        },
        "Input": [
            "$.[businessKey]",
            "$.[amount]",
            {
                "loopCounter": "$.[loopCounter]",
                "element": "$.[element]",
                "throwException" : "$.[mockReduceBalanceFail]"
            }
        ],
        "Output": {
            "compensateReduceBalanceResult": "$.#root"
        },
        "Status": {
            "#root == true": "SU",
            "#root == false": "FA",
            "$Exception{java.lang.Throwable}": "UN"
        },
        "Retry": [
            {
                "Exceptions": ["io.seata.saga.engine.mock.DemoException"],
                "IntervalSeconds": 1.5,
                "MaxAttempts": 3,
                "BackoffRate": 1.5
            },
            {
                "IntervalSeconds": 1,
                "MaxAttempts": 3,
                "BackoffRate": 1.5
            }
        ],
        "Catch": [
            {
                "Exceptions": [
                    "java.lang.Throwable"
                ],
                "Next": "CompensationTrigger"
            }
        ],
        "Next": "Succeed"
    }
    ...
}
```
- **ServiceName**: The name of the service, typically the service's bean ID.
- **ServiceMethod**: The name of the service method.
- **CompensateState**: The compensation "state" for this "state".
- **IsForUpdate**: Indicates if the service will update data. Default is false. If CompensateState is configured, it defaults to true, as services with compensation are typically data update services.
- **IsPersist**: Indicates if execution logs should be stored. Default is true. For some query-type services, it can be set to false. Not storing execution logs improves performance because in case of exception recovery, the service can be re-executed.
- **IsAsync**: Indicates if the service is called asynchronously. Note: Asynchronous service calls will ignore the service's return result, so the service execution status mapping defined by the user (the Status attribute below) will be ignored. It defaults to successful service call. If the asynchronous call submission fails (e.g., thread pool is full), then the service execution status is considered failed.
- **IsRetryPersistModeUpdate**: Indicates if the log is updated based on the last failed log during forward retry. Default is false, meaning a new retry log is added. This has a higher priority than the state machine properties configuration.
- **IsCompensatePersistModeUpdate**: Indicates if the log is updated based on the last compensation log during backward compensation. Default is false, meaning a new compensation log is added. This has a higher priority than the state machine properties configuration.
- **Loop**: Identifies whether the transaction node is a loop transaction, i.e., the framework itself iterates over collection elements based on the configuration of loop attributes and executes the transaction node in a loop. For specific usage, see: [Loop transaction usage](#loop-branch-transaction-usage).
- **Input**: The list of input parameters for calling the service. It's an array corresponding to the service method's parameter list. `$` indicates using an expression to take parameters from the state machine context, expressed using [SpringEL](https://docs.spring.io/spring/docs/4.3.10.RELEASE/spring-framework-reference/html/expressions.html). For constants, the value can be written directly. For how to pass complex parameters, see: [Definition of complex parameters Input](#complex-input-parameters).
- **Output**: Maps the service's returned parameters to the state machine context. It's a map structure where the key is the key when put into the state machine context (the state machine context is also a map), and the value with `$` indicates a SpringEL expression to take values from the service's returned parameters. `#root` represents the entire return parameter of the service.
- **Status**: The mapping of service execution status. The framework defines three statuses: SU (Success), FA (Failure), and UN (Unknown). We need to map the execution status of the service to these three statuses to help the framework judge the consistency of the entire transaction. It's a map structure, where the key is a conditional expression, generally judging from the service's return value or thrown exception. Expressions starting with `$Exception{` indicate judging the type of exception. The value is the mapped execution status when this conditional expression holds.
- **Catch**: Routing after an exception is caught.
- **Retry**: The retry strategy after catching an exception. It's an array that can configure multiple rules. `Exceptions` are the list of matched exceptions, `IntervalSeconds` is the retry interval, `MaxAttempts` is the maximum number of retries, `BackoffRate` is the multiplier for the next retry interval compared to the previous one (e.g., if the last retry interval was 2 seconds, with `BackoffRate=1.5`, the next retry interval will be 3 seconds). The `Exceptions` attribute can be left unconfigured, which means the framework will automatically match network timeout exceptions. If a different exception occurs during the retry process, the framework will rematch the rules and retry according to the new rule, but the total number of retries for the same rule will not exceed its `MaxAttempts`.
- **Next**: The next "state" to execute after the service completes.

> When the Status is not configured to map the execution status of a service, the system automatically determines the status as follows:
>
> - If there is no exception, it is considered a successful execution.
> - If there is an exception, the system checks if the exception is a network connection timeout. If so, it is considered a failure (FA).
> - For other exceptions, if `IsForUpdate=true` for the service, the status is set to unknown (UN); otherwise, it is considered a failure (FA).

> How is the overall execution status of the state machine determined? This is judged by the framework itself, and the state machine has two statuses: `status` (forward execution status) and `compensateStatus` (compensation status):
>
> - If all services execute successfully (transaction commits successfully), then `status=SU`, `compensateStatus=null`.
> - If a service execution fails and there are successfully executed update-type services without compensation (transaction commit fails), then `status=UN`, `compensateStatus=null`.
> - If a service execution fails and there are no successfully executed update-type services without compensation (transaction commit fails), then `status=FA`, `compensateStatus=null`.
> - If compensation is successful (transaction rollback successful), then `status=FA/UN`, `compensateStatus=SU`.
> - If compensation occurs and some services are not successfully compensated (rollback fails), then `status=FA/UN`, `compensateStatus=UN`.
> - In cases of transaction commit or rollback failure, the Seata Server continuously initiates retries.

#### Choice:

```json
"ChoiceState":{
    "Type": "Choice",
    "Choices":[
        {
            "Expression":"[reduceInventoryResult] == true",
            "Next":"ReduceBalance"
        }
    ],
    "Default":"Fail"
}
```

The Choice type of "state" is a single-item selection route:

- **Choices**: A list of optional branches. Only the first branch with a satisfied condition will be chosen.
- **Expression**: A Spring Expression Language (SpringEL) expression.
- **Next**: The next "state" to be executed when the Expression is satisfied.

#### Succeed:

```json
"Succeed": {
    "Type":"Succeed"
}
```

Running into the "Succeed" state indicates that the state machine has ended normally. However, a normal end does not necessarily mean a successful end. Whether it is successful depends on whether each "state" has succeeded.

#### Fail:

```json
"Fail": {
    "Type":"Fail",
    "ErrorCode": "PURCHASE_FAILED",
    "Message": "purchase failed"
}
```

Running into the "Fail" state indicates that the state machine has ended abnormally. During an abnormal termination, you can configure an ErrorCode and Message, representing the error code and error message, respectively. These can be used to return error codes and messages to the caller.

#### CompensationTrigger:

```json
"CompensationTrigger": {
    "Type": "CompensationTrigger",
    "Next": "Fail"
}
```

A CompensationTrigger type of state is used to trigger compensation events and roll back distributed transactions.

- **Next**: The state to which it routes after successful compensation.

#### SubStateMachine:

```json
"CallSubStateMachine": {
    "Type": "SubStateMachine",
    "StateMachineName": "simpleCompensationStateMachine",
    "CompensateState": "CompensateSubMachine",
    "IsRetryPersistModeUpdate": false,
    "IsCompensatePersistModeUpdate": false,
    "Input": [
        {
            "a": "$.1",
            "barThrowException": "$.[barThrowException]",
            "fooThrowException": "$.[fooThrowException]",
            "compensateFooThrowException": "$.[compensateFooThrowException]"
        }
    ],
    "Output": {
        "fooResult": "$.#root"
    },
    "Next": "Succeed"
}
```

The SubStateMachine type of "state" is used for calling a sub-state machine.

- **StateMachineName**: The name of the sub-state machine to be called.
- **CompensateState**: The compensation state of the sub-state machine. It can be left unconfigured, and the system will automatically create its compensation state. The compensation of a sub-state machine actually involves calling the compensate method of the sub-state machine, so the user does not need to implement a compensation service for the sub-state machine themselves. When this attribute is configured, one can use the Input attribute to custom pass some variables, as shown in the CompensateSubMachine below.

#### CompensateSubMachine:

```json
"CompensateSubMachine": {
    "Type": "CompensateSubMachine",
    "Input": [
        {
            "compensateFooThrowException": "$.[compensateFooThrowException]"
        }
    ]
}
```

The CompensateSubMachine type of state is specifically used to compensate a sub-state machine. It calls the compensate method of the sub-state machine. You can use the Input attribute to pass in some custom variables. The Status attribute is used to automatically determine whether the compensation is successful.

#### Complex Input Parameters

```json
"FirstState": {
    "Type": "ServiceTask",
    "ServiceName": "demoService",
    "ServiceMethod": "complexParameterMethod",
    "Next": "ChoiceState",
    "ParameterTypes" : ["java.lang.String", "int", "io.seata.saga.engine.mock.DemoService$People", "[Lio.seata.saga.engine.mock.DemoService$People;", "java.util.List", "java.util.Map"],
    "Input": [
        "$.[people].name",
        "$.[people].age",
        {
            "name": "$.[people].name",
            "age": "$.[people].age",
            "childrenArray": [
                {
                    "name": "$.[people].name",
                    "age": "$.[people].age"
                },
                {
                    "name": "$.[people].name",
                    "age": "$.[people].age"
                }
            ],
            "childrenList": [
                {
                    "name": "$.[people].name",
                    "age": "$.[people].age"
                },
                {
                    "name": "$.[people].name",
                    "age": "$.[people].age"
                }
            ],
            "childrenMap": {
                "lilei": {
                    "name": "$.[people].name",
                    "age": "$.[people].age"
                }
            }
        },
        [
            {
                "name": "$.[people].name",
                "age": "$.[people].age"
            },
            {
                "name": "$.[people].name",
                "age": "$.[people].age"
            }
        ],
        [
            {
                "@type": "io.seata.saga.engine.mock.DemoService$People",
                "name": "$.[people].name",
                "age": "$.[people].age"
            }
        ],
        {
            "lilei": {
                "@type": "io.seata.saga.engine.mock.DemoService$People",
                "name": "$.[people].name",
                "age": "$.[people].age"
            }
        }
    ],
    "Output": {
        "complexParameterMethodResult": "$.#root"
    }
}
```

The definition of the `complexParameterMethod` method is as follows:

```java
People complexParameterMethod(String name, int age, People people, People[] peopleArray, List<People> peopleList, Map<String, People> peopleMap)

class People {

    private String name;
    private int age;

    private People[] childrenArray;
    private List<People> childrenList;
    private Map<String, People> childrenMap;

    ...
}
```

Parameters passed when starting the state machine:

```java
Map<String, Object> paramMap = new HashMap<>(1);
People people = new People();
people.setName("lilei");
people.setAge(18);
paramMap.put("people", people);
String stateMachineName = "simpleStateMachineWithComplexParams";
StateMachineInstance inst = stateMachineEngine.start(stateMachineName, null, paramMap);
```

> Note: The `ParameterTypes` attribute is optional. When the method's parameter list includes Map, List, or other collection types that can have generics, this attribute is needed because Java compilation loses generics information. Therefore, you need to use this attribute. Also, in the Input JSON, add "@type" to declare the generic type (the element type of the collection).

#### Loop Branch Transaction Usage

```json
"States": {
    ...
    "ReduceBalance": {
        "Type": "ServiceTask",
        "ServiceName": "balanceAction",
        "ServiceMethod": "reduce",
        "CompensateState": "CompensateReduceBalance",
        "Loop": {
            "Parallel": 3,
            "Collection": "$.[collection]",
            "ElementVariableName": "loopElement",
            "ElementIndexName": "loopCounter",
            "CompletionCondition": "[nrOfCompletedInstances] / [nrOfInstances] >= 0.6"
        },
        "Input": [
            {
                "loopCounter": "$.[loopCounter]",
                "element": "$.[element]",
                "throwException": "$.[fooThrowException]"
            }
        ],
        "Output": {
            "fooResult": "$.#root"
        },
        "Status": {
            "#root == true": "SU",
            "#root == false": "FA",
            "$Exception{java.lang.Throwable}": "UN"
        },
        "Next": "ChoiceState"
    },
    "ChoiceState": {
        "Type": "Choice",
        "Choices": [
            {
                "Expression": "[loopResult].?[#this[fooResult] == null].size() == 0",
                "Next": "SecondState"
            }
        ],
        "Default":"Fail"
    }
    ...
}
```
- **Loop**: Configuration of the Loop attribute
	- **Parallel**: The number of threads for executing transactions concurrently. It supports concurrent execution of loop tasks, with the default being 1.
		- **Collection**: The collection variable name, an input parameter when the state machine starts, used by the framework to get the collection object that needs to be looped through.
		- **ElementVariableName**: The name of each element in the collection, used to obtain the value of an element in branch transactions. The default is `loopElement`.
		- **CompletionCondition**: Custom condition for ending the loop. If not specified, the default is to execute all, i.e., `[nrOfInstances] == [nrOfCompletedInstances]`.
		- **ElementIndexName**: The name of the collection index, used to obtain the element index in branch transactions. The default is `loopCounter`.

In loop tasks, the output parameters of each transaction are stored in a list: `loopResult`. This list can be accessed in the transaction context to obtain the set of transaction execution results and to iterate over the results of each execution.

- **Loop Context Parameters**
	- **nrOfInstances**: The total number of loop instances.
		- **nrOfActiveInstances**: The total number of currently active instances.
		- **nrOfCompletedInstances**: The total number of instances that have been completed.
		- **loopResult**: The result set of the loop instance executions.

Example State Diagram:

![Saga_Loop Example State Diagram](https://seata.apache.org/assets/images/saga_loop_process-6520c9778e445f4a8340ca78944f09de.png)

## Basic Usage of Saga Annotation Mode

Unlike the AT mode which directly uses data source proxies to shield the details of distributed transactions, business developers need to define their own "execution" and "compensation" for saga resources. For example, in the example below:

```java
@CompensationBusinessAction(name = "DubboSagaActionOne", compensationMethod = "compensation")
    public boolean execute(BusinessActionContext actionContext, @BusinessActionContextParameter(paramName = "param") String param) {
}

@Override
    public boolean compensation(BusinessActionContext actionContext) {
}
```

Seata treats a Saga annotation interface as a Resource, also called a Saga annotation Resource. The core annotation in the business interface is `@CompensationBusinessAction`:

- In the action phase, the business logic of the first phase is executed
- In the compensation phase, when the transaction decides to roll back, the method pointed to by the `compensationMethod` attribute is used to perform custom compensation work.

Additionally, you can use `BusinessActionContext` to pass query parameters in the transaction context in Saga mode. Properties include:

- `xid` global transaction id
- `branchId` branch transaction id
- `actionName` branch resource id (resource id)
- `actionContext` business parameters, which can be annotated with `@BusinessActionContextParameter` to indicate parameters that need to be passed.

After defining the Saga annotation interface, we can start a distributed transaction like in AT mode using `@GlobalTransactional`.

```java
@GlobalTransactional
public String doTransactionCommit(){
   sagabean.exectue(actionContext....)
}
```

## FAQ

**Q:** Can the Saga service process be configured without using a global transaction ID to string everything together, to save on configuration work and avoid errors in manual configuration?

**A:** Saga generally has two implementations: one based on state machine definition, like Apache Camel Saga and Eventuate, and the other based on annotations and interceptors, like ServiceComb Saga. The latter does not require a state diagram configuration. Since Saga transactions do not guarantee isolation, extreme cases like dirty writes might prevent rollback operations. For example, in a distributed transaction, user A is credited before user B's balance is reduced. If user A spends the balance before the transaction is committed and a rollback occurs, compensation becomes impossible. Some business scenarios might allow the business to eventually succeed by continuing retries to complete the process, so the state machine engine provides both "rollback" capability and "forward" capability to recover the context and continue execution, aiming for final consistency. Implementations based on state machines are more common in production. Implementations based on annotations and interceptors will also be provided in the future.

**Q:** If service A is in system 1 and service B is in system 2, and a global transaction is initiated by A calling B to start a subtransaction, does system 2 also need to maintain the three tables of the Saga state machine and configure a StateMachineEngine in the Spring Bean configuration file?

**A:** No, it's not needed. Logs are only recorded by the initiator, and since Saga logs are only recorded by the initiator and the participant services do not have interface parameter requirements, Saga can easily integrate services from other organizations or legacy systems.

**Q:** If services in systems 1 and 2 can call each other and both can initiate global transactions, can they be used in this way? Then, do both systems 1 and 2 need to maintain the three tables of the Saga state machine and configure a StateMachineEngine?

**A:** Yes, they can be used in this way. If both systems initiate Saga transactions, then both would need to record those three tables and configure a StateMachineEngine.

**Q:** When using Seata, it's currently in AT mode. How big would the transformation be if we switched to Saga mode?

**A:** AT mode is completely transparent, whereas Saga is more invasive as it requires configuration of the state machine JSON. If there are many services, the transformation could be substantial.

**Q:** Is Saga mode an enhancement of long transaction processing based on AT mode?

**A:** No, it's not based on AT. The client sides are completely separate, though the server side is reused. You can see many examples in Saga's unit tests: [https://github.com/apache/incubator-seata/tree/develop/test/src/test/java/io/seata/saga/engine](https://github.com/apache/incubator-seata/tree/develop/test/src/test/java/io/seata/saga/engine)

**Q:** In the developer documentation, the state machine engine's principle diagram shows an EventQueue that is used only for initiating distributed transactions and calling other system services as if calling local services. Are the systems still using RPC calls? And is it not purely event-driven between systems? (By "purely event-driven between systems," I mean even RPC is non-blocking.)

**A:** Nodes are event-driven between each other. Non-blocking RPC requires support from the RPC client, which is theoretically possible. If the RPC client is also non-blocking IO, then all aspects are asynchronous.

**Q:** Consider a business process where subsequent sub-processes, regardless of which runs first, do not affect each other and can be called asynchronously. These sub-processes are services of other systems. Has Seata Saga implemented this, and are the individual nodes asynchronous in Saga's asynchronous calls?

**A:** The asynchronous start of a state machine (stateMachineEngine.startAsync) means that all states within the state machine are executed driven by events. The entire process is actually synchronous; the next state's event is generated only after the previous state ends. However, calling a service asynchronously is configuring that ServiceTask as "IsAsync": true. This service will be called asynchronously and will not block the progress of the state machine, which does not care about its execution result.

**Q:** What are the roles of the synchronous bus and asynchronous bus in the event-driven layer of Saga's source code?

**A:** The synchronous BUS is thread-blocking and returns only after the entire state machine has finished executing. The asynchronous BUS is non-thread-blocking; it returns immediately after the call, and the state machine engine calls back your Callback after it has finished executing.

**Q:** IsPersist: Does the execution log get stored? It's true by default, but some query-type services can be configured to false, so the execution log is not stored to improve performance, as services can be re-executed in case of exception recovery, right?

**A:** Yes, it can be configured to false. However,

it's recommended to keep the default initially for a complete query execution log. Performance tuning can be considered later if needed; generally, there shouldn't be performance issues.

**Q:** For seata saga, if the client initiating the transaction or the seata server side crashes or restarts, how are unfinished state machine instances ensured to continue execution? Who triggers this operation?

**A:** State machine instances are logged in the local database and recovered through these logs. The seata server triggers transaction recovery.

**Q:** Does Saga's JSON file support hot deployment?

**A:** Yes, it supports hot deployment. You can use stateMachineEngine.getStateMachineConfig().getStateMachineRepository().registryByResources(). However, Java code and services need to implement support for hot deployment themselves.

**Q:** If both inputs and outputs are placed in Saga's context, and if there are many or large parameters and a large volume of business, are there any memory limitations?

**A:** There are no limitations set. It's recommended not to put irrelevant parameters into the context. Parameters needed by the next service or for branch judgment can be put into the context.

**Q:** Just to confirm: Each node either handles exceptions internally to ensure there are return messages, or does not handle them internally and lets the state machine engine catch exceptions, defining the Catch attribute in JSON. So, compensation nodes do not automatically trigger compensation; manual intervention is needed in JSON, routing to CompensationTrigger through Catch or Choices attributes, right?

**A:** Yes, that's correct. This design is to increase flexibility. Users can control whether to roll back because not all exceptions require rollback; there may be some custom handling methods.

**Q:** So Catch and Choices can be freely routed to the desired state, right?

**A:** Yes. This custom compensation triggering design is based on BPMN 2.0.

**Q:** Regarding the JSON file, I plan to define one JSON for one process. Even though some processes are similar and can be solved with Choices, I feel the JSON should be as simple as possible. Is this consideration correct?

**A:** You can consider using a sub-state machine for reuse. A sub-state machine will generate an additional line of stateMachineInstance records, but the impact on performance should be minimal.
