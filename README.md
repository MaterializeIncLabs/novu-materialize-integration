# novu-materialize-integration
Standard container for integrating a Materialize subscription with Novu

## About

### What is Novu?
Novu is an open-source notification infrastructure designed to streamline the process of managing and delivering notifications across various channels such as email, SMS, Slack, in-app messages, and push notifications. It provides a unified API, customizable workflows, and templates to help developers integrate and manage notifications in their applications efficiently. 

### Why integrate Materialize with Novu?
Materialize specializes in Operational data, which we consider to be your organizations freshest data that is being used to make intra-day decisions about what is impacting your business right now. Because of this, operational data is often very useful for things like alerting, real-time notification, and automation. 

### Materialize, Novu and Event-Driven Architecture
Materialize is unique to other databases in it's ability to participate in [event-driven architectures](https://en.wikipedia.org/wiki/Event-driven_architecture). This means that you do not need to poll Materialize continually for changes in the data. Materialize updates your analytical views in real-time, and when they change will push the data to your downstream systems. The primary means for this interaction are with either Sinks or Subscribes. Sinks allow you to push data externally to message brokers like Kafka, where subscriptions allow you to make long-lived connections to Materialize via the Postgres protocol and libraries to recieve the updates. This integration uses the later method, using a Python Docker container to subscribe to Materialize and publish every message that enters your Materialize view into a Novu workflow for integration with a wide variety of notification systems.

## Setup & Configuration

### Overview
This is the code for a Python docker container that implements a "durable" subscribe to an indexed or materialized view. Every time that view updates, it will trigger the Novu event API with the configured payload from the Materialize data. 

Requirements:
    - A Novu cloud account ( or some other Novu install ) with a configured workflow for the desired alert
    - A Materialize account with a view configured for the alert and  adquate rights to subscribe to the view
    - Somewhere to continually run this container ( ex. AWS ECS, Kubernetes, etc )

### Configuration
All configuration of the container is done using environment variables. They can be set using a .env file in the same directory, which is probably not secure in production, or some other method supported by your container platform.

#### Required

| Environment Variable    | Description                                                                                | Default |
| ----------------------- | ------------------------------------------------------------------------------------------ | ------- |
| `NOVU_API_KEY`          | The secret API key for your Novu install                                                   | None    |
| `NOVU_WORKFLOW_NAME`    | The name of the Novu workflow for your alert                                               | None    |
| `NOVU_RECIPIENTS`       | comma sep list of recipient IDs (reqd if no `RECIPIENTS_IN_PAYLOAD`)                       | None    |
| `RECIPIENTS_IN_PAYLOAD` | payload column that contains list of recipients (reqd if no `NOVU_RECIPIENTS`)             | None    |
| `MTZ_USER`              | The user account to connect to Materialize                                                 | None    |
| `MTZ_PASSWORD`          | The seucure app password for your Materialize connection                                   | None    |
| `MTZ_HOST`              | Your Materialize hostname                                                                  | None    |
| `MTZ_ALERT_VIEW`        | The name of the view to query in Materialize                                               | None    |
| `MTZ_PERSIST_TABLE`     | The name of the table to persist the last-ready timestamp to                               | None    |
| `MTZ_ALERT_PAYLOAD`     | Comma-seperated list of  fields to select from the alert view, also passed in Novu payload | None    |

#### Optional

| Environment Variable       | Description                                                            | Default             |
| -------------------------- | ---------------------------------------------------------------------- | ------------------- |
| `SEND_RETRACTIONS`         | Deleted records in Materialize should be sent to Novu as alert deletes | False               |
| `NOVU_URL`                 | The URL to your Novu installation                                      | https://api.novu.co |
| `MTZ_CLUSTER`              | The name of the cluster to use in Materialize                          | quickstart          |
| `MTZ_DATABASE`             | The Materialize database to connect to                                 | materialize         |
| `RETAIN_HISTORY`           | The minutes of history your view is set to retain ( recovery window)   | 60                  |
| `PROGRESS_TIMEOUT_MINUTES` | Minutes after which to fail if 1-second progress messages not recv'd   | 10                  |
| `LOG_LEVEL`                | Level of logs to report ( INFO/WARNING/ERROR)                          | WARNING             |
| `LOG_OUTPUT`               | Where to send logs (stdout/stderr/filename)                            | stdout              |
| `TEST_MODE`                | Run without sending any alerts to Novu for testing                     | False               |

### Operation

#### Getting The Docker Container
- The docker container can be built locally from the Python folder
  - `docker build -t novu-materialize-integration`
- The docker container is also published in the AWS Container Registry 
  - `400121260767.dkr.ecr.us-east-1.amazonaws.com/novu-materialize-integration:latest`
  - TODO: make this public / publish to Docker Hub

#### Running

- Once the container has been configured with the above environment variables, it can be run statelessly from any container management platform ( or locally ).
- `TEST_MODE` may be useful for testing out your alert views prior to publishing to Novu.
- `LOG_LEVEL` can be set to INFO for detailed debugging.
- In the event of container failure, apon reconnection you will generate all the alerts that you missed as long as it's within the `RETAIN_HISTORY` window of minutes. Otherwise it will pick up from the current time.
- Any fields defined in `MTZ_ALERT_PAYLOAD` must be present in the `MTZ_ALERT_VIEW` view, and will be sent in the payload of the alert to Novu.
- Novu docs are at https://docs.novu.co

### SQL Examples
The examples in the sql/ folder are based on the [`winning_bids` table in Materialize quickstart (auction house) example](https://materialize.com/docs/get-started/quickstart/#step-2-use-indexes-for-speed).

- The simple view shows the simplest way to create views for this integration, where you build views 1-1 with the specific alert thresholds coded in them. In this case, you would get an "expensive pizza" alert every time an auction closed for "Best Pizza In Town" above $90. You would set `MTZ_ALERT_VIEW` to `materialize.auction.expensive_pizza` and `MTZ_ALERT_PAYLOAD` to `item,amount` and you are done.
- Lateral view shows a slightly more sophisticated pattern that you might use if you have multiple alerts of a similar kind. You can populate your `WHERE` clause from a `LATERAL JOIN` to an `auction_alerts` table. Here each row of the `auction_alerts` table will create it's own named alert with it's own thresholds, and you can potentially filter them out by alert name on the Novu side if you want different handling using Novu [step conditions[(https://docs.novu.co/workflows/step-filters)].
  - This example creates two alerts, the first is `expensive_pizza` and behaves the same as the simple example. The second is `all art`, which alerts on all auctions that close for _Custom Art_ as long as the price is above zero.
  - The important detail here is that we can modify, delete, or add different alerts in real-time without re-deploying to Materialize or operational interruption by modifying the rows of the `auction_alerts` table
  - In this example, `MTZ_ALERT_VIEW` is `materialize.auction.auction_alerts` and `MTZ_ALERT_PAYLOAD` is `alert_name,auction_id,item_name,price`
  - On the Novu side, you could have different step conditions based on whether the `alert_name` in the payload is "expensive pizza" or "all art"
