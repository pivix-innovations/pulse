# Pulse


## High Level Architecture
```

                                    +----------------------+
                                    |     Sentinel         |
                                    | Users / Tenants      |
                                    +----------+-----------+
                                               |
                                        User & Tenant Lookup
                                               |
                                               |
    +-------------+      Publish Event          +---v-------------------+
    | Quotient    | --------------------------> | Notification API      |
    +-------------+                             | (FastAPI)             |
                                                +-----------+-----------+
                                                            |
                                                 Authentication / Validation
                                                            |
                                                +-----------v-----------+
                                                | Notification Service  |
                                                +-----------+-----------+
                                                            |
                             +------------------------------+------------------------------+
                             |                              |                              |
                     Workflow Resolver             Preference Resolver            Recipient Resolver
                             |                              |                              |
                             +------------------------------+------------------------------+
                                                            |
                                                   Notification Builder
                                                            |
                                                   Render Templates
                                                            |
                                                   Persist Notification
                                                            |
                                                   Publish Job
                                                            |
                                                   Message Queue
                                                            |
                                    +----------------+---------------+---------------+
                                    |                |                               |
                                Email Worker      Push Worker                    SMS Worker
                                    |                |                               |
                                Email Driver     Push Driver                    SMS Driver
                                    |                |                               |
                                SES/SMTP/Resend   FCM / APNS                    Twilio / MSG91
                                    |
                                Delivery Status
                                    |
                            Notification Repository
                                    |
                        REST API / Websocket / Future Analytics
```

## Domain model
### Notification
```
├── Recipient
├── Event
├── Workflow
├── Template
├── Channel
├── Provider
├── DeliveryAttempt
└── Preferences
```

## Directory structure
```
pulse/
├── app/
│
├── api/
│   ├── v1/
│   │   ├── notifications.py
│   │   ├── templates.py
│   │   ├── workflows.py
│   │   ├── preferences.py
│   │   └── providers.py
│   │
│   └── dependencies.py
│
├── core/
│   ├── config.py
│   ├── constants.py
│   ├── exceptions.py
│   ├── logging.py
│   ├── security.py
│   └── lifecycle.py
│
├── domain/
│   ├── entities/
│   │   ├── notification.py
│   │   ├── workflow.py
│   │   ├── template.py
│   │   ├── provider.py
│   │   ├── delivery.py
│   │   ├── recipient.py
│   │   └── preference.py
│   │
│   ├── value_objects/
│   │   ├── channel.py
│   │   ├── event.py
│   │   ├── payload.py
│   │   └── metadata.py
│   │
│   └── enums/
│
├── services/
│
│   ├── notification_service.py
│   ├── workflow_service.py
│   ├── template_service.py
│   ├── rendering_service.py
│   ├── recipient_service.py
│   ├── preference_service.py
│   ├── delivery_service.py
│   ├── scheduling_service.py
│   └── metrics_service.py
│
├── workflows/
│   ├── registry.py
│   ├── resolver.py
│   ├── compiler.py
│   └── definitions/
│       ├── quotient/
│       ├── sentinel/
│       └── common/
│
├── templates/
│   ├── email/
│   ├── push/
│   ├── sms/
│   └── in_app/
│
├── providers/
│
│   ├── email/
│   │   ├── base.py
│   │   ├── smtp.py
│   │   ├── ses.py
│   │   ├── resend.py
│   │   └── factory.py
│   │
│   ├── push/
│   │   ├── base.py
│   │   ├── firebase.py
│   │   ├── apns.py
│   │   └── factory.py
│   │
│   ├── sms/
│   │   ├── base.py
│   │   ├── twilio.py
│   │   ├── msg91.py
│   │   └── factory.py
│   │
│   └── registry.py
│
├── renderers/
│   ├── jinja_renderer.py
│   └── markdown_renderer.py
│
├── queue/
│   ├── producer.py
│   ├── consumer.py
│   ├── retry.py
│   └── dead_letter.py
│
├── workers/
│   ├── email_worker.py
│   ├── push_worker.py
│   ├── sms_worker.py
│   └── scheduler_worker.py
│
├── repositories/
│   ├── notification_repository.py
│   ├── workflow_repository.py
│   ├── template_repository.py
│   ├── preference_repository.py
│   └── delivery_repository.py
│
├── database/
│   ├── models/
│   ├── migrations/
│   └── session.py
│
├── clients/
│   ├── sentinel_client.py
│   └── websocket_client.py
│
├── events/
│   ├── publisher.py
│   ├── subscriber.py
│   └── schemas.py
│
├── websocket/
│   ├── manager.py
│   └── broadcaster.py
│
├── telemetry/
│   ├── metrics.py
│   ├── tracing.py
│   └── health.py
│
└── tests/
```