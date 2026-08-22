# 🏫 AR School Management System

**AR School Management System** is a professional desktop-based school management platform built with **Python and Tkinter**.

It brings essential school operations into one unified system, including **student management, admissions, attendance, fees, results, teachers, payroll, accounting, timetable, reports, academic years, user permissions, backup, and audit logging**.

> **AR Software Solutions — Smart Software. Simple Solutions.**

Designed with a clean and modern interface, the system focuses on **usability, organization, security, and professional desktop application design** for school administration.

---

## 📸 Software Preview

### 🔐 Login System

Secure role-based login interface supporting **Admin, Teacher, and Reception** users.

<img width="460" height="552" alt="Login Screen" src="https://github.com/user-attachments/assets/34f85ffa-e968-403b-9d41-76186e6f9158" />

---

### 📊 Dashboard

A modern dashboard providing a quick overview of students, teachers, attendance, fees, revenue, expenses, and common school operations.

<img width="1366" height="768" alt="Dashboard" src="https://github.com/user-attachments/assets/96e7dbb7-a22e-478b-8bfc-ad8d5dc891e0" />

---

### 🧑‍🎓 Student Management

Centralized student management with search, filtering, admissions, profiles, fee information, academic records, and ID card generation.

<img width="980" height="768" alt="Student Management" src="https://github.com/user-attachments/assets/20881ada-8958-4d43-a496-42021f4c566e" />

---

### 🕒 Attendance Management

Manual and scan/code-based attendance with Present, Absent, Leave, and Late tracking.

<img width="1366" height="768" alt="Attendance Management" src="https://github.com/user-attachments/assets/02b7ffd5-63bd-4a05-a174-0570daea2cec" />

---

### 📝 Results & Academics

Manage student marks, exams, grading, academic performance, and PDF marksheets.

<img width="1366" height="768" alt="Results & Academics" src="https://github.com/user-attachments/assets/89ece614-f92a-490b-84f3-3a4a3e400617" />

---

### 👩‍🏫 Teachers & Payroll

Manage teachers, attendance, salaries, and professional payslips from one dedicated interface.

<img width="1180" height="752" alt="Teachers & Payroll" src="https://github.com/user-attachments/assets/48ad21bf-a9ec-4c4c-9611-6a2f980d1713" />

---

### 🗓️ Timetable Management

Create and manage class schedules using days, time slots, subjects, and teachers.

<img width="1366" height="768" alt="Timetable Management" src="https://github.com/user-attachments/assets/49225693-89ec-460b-b905-269ebbe62a54" />

---

### 💰 Finance & Accounting

Track school revenue, expenses, fees, salaries, and financial records through an integrated accounting system.

<img width="1366" height="768" alt="Finance Management" src="https://github.com/user-attachments/assets/8a26a771-f2c0-43e1-84cf-1ca7bdd8141d" />

---

### 🏫 School Profile & Configuration

Manage school identity, branding, users, permissions, academic years, backups, and system settings.

<img width="1366" height="768" alt="School Settings" src="https://github.com/user-attachments/assets/fb0ae2c3-2c95-48b6-bfcc-d452ead87cdb" />

---

### 🛡️ Security & Audit Logs

Track supported user and administrative actions with usernames and timestamps for accountability.

<img width="1366" height="768" alt="Security and Audit Logs" src="https://github.com/user-attachments/assets/af7586c7-10bc-4a3e-beee-457fd0dec051" />

---

# ✨ Key Features

### 🧑‍🎓 Student Management

* Student directory
* Search and filtering
* Student admission
* Student profiles
* Fee information
* Academic records
* ID card generation
* Student archive and restore

### 🕒 Attendance

* Manual attendance
* Scan/code-based attendance
* Present / Absent / Leave / Late tracking
* Attendance history

### 💵 Fee Management

* Fee collection
* Paid and pending fee tracking
* Fee records
* PDF fee receipts
* Accounting integration

### 📝 Results & Academics

* Marks entry
* Exam management
* Grading system
* Pass/fail rules
* Academic performance
* PDF marksheets

### 👩‍🏫 Teachers & Payroll

* Teacher registration
* Teacher directory
* Teacher attendance
* Salary management
* Payslip generation

### 🗓️ Timetable

* Class scheduling
* Day and time-slot management
* Subject assignment
* Teacher assignment

### 💰 Finance & Accounting

* Revenue tracking
* Expense tracking
* Fee integration
* Salary integration
* Financial dashboard

### 📅 Academic Years

* Academic year management
* Current year selection
* Student year enrollment
* Academic year records

### 🔐 Security

* Role-Based Access Control
* Admin / Teacher / Reception roles
* Permission management
* Password hashing
* Audit logging

### 💾 Backup & Data Safety

* SQLite database
* Local backups
* Timestamped backups
* USB backup support
* Safe data operations

### 🤖 AI Admin Assistant

* Local database-grounded assistant
* Student statistics
* Fee information
* Attendance information
* Revenue and expense information
* Results information
* Teacher attendance queries
* Supported English and Roman Urdu queries

---

# 🎨 Professional UI & Design

AR School Management System is designed with a **modern, clean, and consistent desktop interface**.

The application uses:

* Centralized UI theme
* Consistent colors and typography
* Professional dashboard cards
* Structured navigation
* Responsive data tables
* Clear forms and dialogs
* Role-based interface controls
* Consistent buttons and icons
* User-friendly administrative workflows

The goal is to provide a **professional desktop experience rather than a basic Tkinter interface**.

---

# 🛠️ Technology Stack

| Category        | Technology              |
| --------------- | ----------------------- |
| Language        | Python 3                |
| GUI             | Tkinter                 |
| Database        | SQLite                  |
| Authentication  | PBKDF2 Password Hashing |
| Access Control  | RBAC                    |
| Reporting       | PDF Generation          |
| Barcode         | Code128 Support         |
| QR Code         | QR Generation           |
| Version Control | Git & GitHub            |

---

# 📁 Project Structure

```text
AR-School-Management-System/
│
├── ar_school_pack/
│   ├── app.py
│   ├── db.py
│   ├── security.py
│   ├── rbac.py
│   ├── branding.py
│   ├── accounting.py
│   ├── results_engine.py
│   ├── reports.py
│   ├── theme.py
│   ├── ai_assistant.py
│   ├── academic_year.py
│   ├── student_lifecycle.py
│   ├── requirements.txt
│   └── ...
│
├── .gitignore
├── LICENSE
└── README.md
```

### Core Modules

| Module                 | Purpose                                  |
| ---------------------- | ---------------------------------------- |
| `app.py`               | Main application and user interface      |
| `db.py`                | SQLite database management               |
| `security.py`          | Authentication and password security     |
| `rbac.py`              | Roles and permissions                    |
| `accounting.py`        | Finance and accounting                   |
| `results_engine.py`    | Grading and result rules                 |
| `reports.py`           | PDF reports and documents                |
| `theme.py`             | Centralized UI design system             |
| `ai_assistant.py`      | Local AI Admin Assistant                 |
| `academic_year.py`     | Academic year management                 |
| `student_lifecycle.py` | Student archive and lifecycle management |

---

# 🚀 Quick Start

```bash
git clone https://github.com/abdulrehman1-eng/AR-School-Management-System.git
cd AR-School-Management-System/ar_school_pack
pip install -r requirements.txt
python app.py
```

> **Requirements:** Python 3.x and Tkinter.

---

# 🤖 AI Admin Assistant

The built-in assistant works with the school's local database to answer supported administrative questions.

Example:

```text
How many students are enrolled?

Which students have pending fees?

What is today's attendance?

How much revenue was recorded?

How much expense was recorded?
```

The assistant is designed to provide database-based answers rather than inventing school information.

---

# 🏫 Demo Data

The screenshots use **sample/demo school data**.

### Demo School

**AR Academy**

No real student information, production passwords, API keys, or private school data should be included in the public repository.

---

# 🔒 Security

The application includes:

* Password hashing
* Role-Based Access Control
* Permission management
* Audit logging
* Database backups
* Safe destructive operations
* Local SQLite data storage

> Never publish real passwords, API keys, tokens, or private student information in a public repository.

---

# 📌 Project

**AR School Management System** is an actively developed desktop school-management project created as part of the **AR Software Solutions** software portfolio.

The project demonstrates practical experience in:

**Python • Tkinter • SQLite • Database Design • RBAC • Authentication • PDF Reports • Desktop UI/UX • Software Architecture**

---

# 👨‍💻 Author

## Abdul Rehman

**AR Software Solutions**

GitHub: **[@abdulrehman1-eng](https://github.com/abdulrehman1-eng)**

> **Smart Software. Simple Solutions.**

---

# ⭐ Support the Project

If you find this project interesting:

⭐ Star the repository
🐛 Report bugs
💡 Suggest improvements
🤝 Follow the development

---

## 📄 License

**Copyright © 2026 AR Software Solutions. All rights reserved.**

This software is proprietary. Unauthorized copying, commercial use, redistribution, or resale is not permitted without written permission from AR Software Solutions.
