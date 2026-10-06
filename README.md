&#x09;# 🤖 AI CRM – Artificial Intelligence-Based Customer Relationship Management System



> A secure, intelligent, and user-friendly Customer Relationship Management system built with Django, SQLite, and Artificial Intelligence for lead conversion prediction and business management.



\---



\## 📌 Project Overview



\*\*AI CRM (Artificial Intelligence-Based Customer Relationship Management System)\*\* is a web-based CRM application designed to help organizations manage customers, sales activities, leads, products/services, interactions, reminders, invoices, payments, and business reports from a centralized platform.



The system integrates \*\*Artificial Intelligence-based lead scoring\*\* to analyze lead information and estimate the probability of conversion. It uses a \*\*Machine Learning model when sufficient data is available\*\*, with a \*\*rule-based fallback mechanism\*\* when there is not enough training data.



The project focuses on:



\* Customer relationship management

\* Lead management

\* AI-assisted lead conversion prediction

\* Sales and payment management

\* Product and service management

\* Reminders and interactions

\* Notifications

\* Business reports

\* Role-based access control

\* Activity and audit logging

\* Data validation and security



This project was developed as a \*\*final-year B.Voc Software Development and System Administration project\*\*.



\---



\## 🎯 Objectives



The main objectives of AI CRM are to:



1\. Centralize customer and business information.

2\. Improve customer relationship management.

3\. Track and manage leads efficiently.

4\. Predict lead conversion probability using AI.

5\. Help sales staff prioritize high-potential leads.

6\. Manage products, services, and stock.

7\. Record sales, invoices, and payments.

8\. Schedule reminders and track customer interactions.

9\. Provide useful business reports and analytics.

10\. Implement secure role-based access control.

11\. Maintain activity and audit logs.

12\. Provide a professional and user-friendly interface.



\---



\## ✨ Key Features



\### 👥 Customer Management



\* Add and manage customer information.

\* Maintain customer profiles.

\* View customer-related activities.

\* Track interactions and sales information.

\* Protect customer data using server-side permissions.



\### 🎯 Lead Management



\* Create and manage leads.

\* Track lead status and progress.

\* Assign leads to sales staff.

\* Maintain lead status history.

\* Convert qualified leads into customers.

\* Archive inactive leads.

\* Record scoring information.



\### 🤖 AI Lead Scoring



The system provides an AI-assisted lead conversion score.



The scoring system can use:



\* Lead information

\* Customer-related attributes

\* Historical conversion data

\* Logistic Regression machine-learning model

\* Rule-based/heuristic fallback



The system provides a conversion probability that helps sales staff identify leads that are more likely to become customers.



\### 🛒 Product \& Service Management



\* Add products and services.

\* Organize items using categories.

\* Track stock.

\* Manage stock adjustments.

\* Validate stock-related operations.

\* Support product images.

\* Prevent invalid stock operations.



\### 💰 Sales Management



\* Create sales records.

\* Manage sale items.

\* Generate invoices.

\* Track payments.

\* Maintain sales history.

\* Protect related financial records from accidental deletion.



\### 🔔 Reminders



\* Create customer-related reminders.

\* Track reminder status.

\* Help sales staff follow up with customers on time.



\### 💬 Customer Interactions



Record different customer interactions and maintain a history of communication and activities.



\### 🔔 Notifications



The system provides notifications for important events and user activities.



\### 📊 Reports \& Analytics



The reporting system provides useful business information such as:



\* Customer statistics

\* Lead statistics

\* Sales information

\* Revenue information

\* Product/service information

\* Business performance data



Reports can be processed and exported using supporting libraries.



\### 🔐 Authentication \& Role-Based Access Control



The system supports different user roles:



\#### 👑 Administrator



Administrators can manage and monitor major system operations, including:



\* Users

\* Sales staff

\* Customers

\* Leads

\* Products/services

\* Categories

\* Reports

\* System activities



\#### 👨‍💼 Sales Staff



Sales staff can:



\* Manage assigned leads

\* Manage customer interactions

\* Add reminders

\* Track sales activities

\* Work with customer and lead information

\* Use AI-assisted lead scoring



\#### 👤 Customer



Customers have access to customer-specific information and functionality according to their permissions.



All important permissions are enforced \*\*server-side\*\*, rather than relying only on interface-level restrictions.



\---



\## 🧠 AI Lead Scoring Architecture



The AI component is designed to work in two stages.



```text

&#x20;               Lead Information

&#x20;                      │

&#x20;                      ▼

&#x20;             Feature Preparation

&#x20;                      │

&#x20;                      ▼

&#x20;            Is enough training data

&#x20;                   available?

&#x20;                 /          \\

&#x20;               Yes           No

&#x20;                │             │

&#x20;                ▼             ▼

&#x20;       Logistic Regression   Rule-Based

&#x20;            Model            Scoring

&#x20;                │             │

&#x20;                └──────┬──────┘

&#x20;                       ▼

&#x20;             Conversion Score

&#x20;                       │

&#x20;                       ▼

&#x20;              Lead Prioritization

```



\### Machine Learning



When sufficient historical data is available, the system can use \*\*Logistic Regression\*\* to estimate the probability of lead conversion.



\### Rule-Based Fallback



When there is insufficient data to train a reliable machine-learning model, the system uses a rule-based scoring mechanism.



This approach makes the system more practical during the early stages of deployment when historical CRM data may be limited.



\---



\## 🏗️ System Architecture



The project follows a modular Django architecture.



```text

AI CRM

│

├── accounts

├── dashboard

├── customers

├── staff

├── leads

├── catalog

├── sales

├── reminders

├── interactions

├── notifications

├── activity\_logs

├── reports

├── ai\_assistant

├── core

└── config

```



\### Major Components



| Module          | Purpose                                  |

| --------------- | ---------------------------------------- |

| `accounts`      | Authentication and user management       |

| `dashboard`     | Dashboard and system overview            |

| `customers`     | Customer management                      |

| `staff`         | Sales staff management                   |

| `leads`         | Lead management and conversion           |

| `catalog`       | Products, services, categories and stock |

| `sales`         | Sales, invoices and payments             |

| `reminders`     | Follow-up reminders                      |

| `interactions`  | Customer interaction records             |

| `notifications` | System notifications                     |

| `activity\_logs` | Audit and activity tracking              |

| `reports`       | Business reports and analytics           |

| `ai\_assistant`  | AI-related functionality                 |

| `core`          | Shared/core application functionality    |

| `config`        | Django project configuration             |



\---



\## 🛠️ Technology Stack



\### Backend



\* \*\*Python\*\*

\* \*\*Django 5.2\*\*

\* Django ORM



\### Frontend



\* HTML5

\* CSS3

\* JavaScript

\* Django Templates



\### Database



\* \*\*SQLite\*\*



\### Artificial Intelligence / Machine Learning



\* Scikit-learn

\* Logistic Regression

\* Rule-based heuristic scoring



\### Data Processing



\* Pandas

\* OpenPyXL



\### Reporting



\* ReportLab

\* PDF report generation



\### Security



\* Django authentication

\* Role-Based Access Control (RBAC)

\* Server-side authorization

\* Password hashing

\* CSRF protection

\* Input validation

\* Audit logging



\---



\## 🔒 Security Features



Security was considered throughout the application architecture.



Key security measures include:



\* Authentication and authorization

\* Role-based access control

\* Server-side permission checks

\* Django CSRF protection

\* Password hashing

\* Form and model validation

\* Secure database relationships

\* Audit/activity logging

\* Protected related records

\* Environment-based configuration

\* `.env` support for sensitive configuration

\* Prevention of unauthorized operations



The application also uses protected database relationships where deleting a record could affect important business data.



\---



\## 🧪 Testing



The project includes an extensive automated test suite covering major application functionality.



\### Current Test Coverage



\*\*295 automated tests\*\*



Testing includes areas such as:



\* Authentication

\* User roles

\* Permissions

\* Customer management

\* Staff management

\* Lead management

\* Lead conversion

\* AI scoring

\* Product management

\* Stock management

\* Sales

\* Invoices

\* Payments

\* Reminders

\* Interactions

\* Notifications

\* Reports

\* Activity logs

\* Validation

\* Security-related behavior



Run the test suite using:



```bash

python manage.py test

```



\---



\## 📁 Project Structure



A simplified project structure is shown below:



```text

AI-CRM/

│

├── accounts/

├── activity\_logs/

├── ai\_assistant/

├── catalog/

├── config/

├── core/

├── customers/

├── dashboard/

├── interactions/

├── leads/

├── notifications/

├── reminders/

├── reports/

├── sales/

├── staff/

│

├── static/

├── templates/

│

├── manage.py

├── requirements.txt

├── .env.example

├── .gitignore

└── README.md

```



> Local databases, environment files, virtual environments, backups, generated exports, and other sensitive/runtime files are excluded from version control through `.gitignore`.



\---



\## ⚙️ Installation \& Setup



\### 1. Clone the Repository



```bash

git clone https://github.com/amnaav/AI-CRM.git

```



Move into the project directory:



```bash

cd AI-CRM

```



\---



\### 2. Create a Virtual Environment



Windows:



```powershell

python -m venv .venv

```



Activate it:



```powershell

.venv\\Scripts\\activate

```



Linux/macOS:



```bash

python3 -m venv .venv

source .venv/bin/activate

```



\---



\### 3. Install Dependencies



```bash

pip install -r requirements.txt

```



\---



\### 4. Configure Environment Variables



Create a `.env` file based on the provided example:



```text

.env.example

```



Add the required configuration values.



> Do not upload private credentials, API keys, passwords, or other secrets to GitHub.



\---



\### 5. Apply Database Migrations



```bash

python manage.py migrate

```



\---



\### 6. Create an Administrator Account



```bash

python manage.py createsuperuser

```



Follow the terminal instructions to create the administrator account.



\---



\### 7. Run the Development Server



```bash

python manage.py runserver

```



Open the application in your browser:



```text

http://127.0.0.1:8000/

```



\---



\## 🧪 Running Tests



Run all automated tests:



```bash

python manage.py test

```



For more detailed output:



```bash

python manage.py test -v 2

```



\---



\## 📊 Example CRM Workflow



A typical workflow in the system is:



```text

New Lead

&#x20;  │

&#x20;  ▼

Lead Information Added

&#x20;  │

&#x20;  ▼

AI Conversion Scoring

&#x20;  │

&#x20;  ▼

Lead Prioritized

&#x20;  │

&#x20;  ▼

Sales Staff Follow-up

&#x20;  │

&#x20;  ▼

Customer Interaction

&#x20;  │

&#x20;  ▼

Lead Converted

&#x20;  │

&#x20;  ▼

Customer

&#x20;  │

&#x20;  ▼

Sale

&#x20;  │

&#x20;  ▼

Invoice

&#x20;  │

&#x20;  ▼

Payment

```



\---



\## 🖥️ Screenshots



Screenshots can be added here to demonstrate the application's interface.



Suggested screenshots:



1\. Login Page

2\. Admin Dashboard

3\. Sales Staff Dashboard

4\. Customer Dashboard

5\. Customer Management

6\. Lead Management

7\. AI Lead Scoring

8\. Product \& Stock Management

9\. Sales Management

10\. Reports

11\. Activity Logs



Example:



```markdown

!\[Dashboard](screenshots/dashboard.png)

```



\---



\## 📈 Future Enhancements



Possible future improvements include:



\* Advanced AI lead prediction

\* Larger machine-learning datasets

\* More ML algorithms

\* Customer churn prediction

\* Sales forecasting

\* Sentiment analysis

\* Email integration

\* WhatsApp/communication integration

\* Advanced analytics dashboards

\* Cloud deployment

\* REST API

\* Mobile application

\* Automated follow-up recommendations

\* Real-time notifications

\* Advanced customer segmentation



\---



\## 🎓 Academic Information



\*\*Project Title:\*\*

AI CRM – Artificial Intelligence-Based Customer Relationship Management System



\*\*Program:\*\*

B.Voc Software Development and System Administration



\*\*Academic Year:\*\*

2026–2027



\*\*Institution:\*\*

MES College Marampally, Aluva



\*\*Project Type:\*\*

Final-Year Academic Project



\---



\## 👩‍💻 Developer



\*\*Amna\*\*



B.Voc Software Development and System Administration

MES College Marampally, Aluva



\---



\## 📜 Project Status



🟢 \*\*Active Development\*\*



The project is being developed and improved as a final-year academic project with a focus on:



\* Software development

\* Artificial Intelligence

\* Database management

\* System administration

\* Web application security

\* Testing

\* Real-world CRM workflows



\---



\## ⭐ Acknowledgement



This project was developed as part of the academic requirements for the \*\*B.Voc Software Development and System Administration\*\* program.



The project provided practical experience in software development, Django web application development, database management, artificial intelligence, security, testing, and system design.



\---



\## 📄 License



This project is intended primarily for \*\*academic and educational purposes\*\*.



If you wish to reuse, modify, or distribute this project, please provide appropriate attribution to the original developer.



