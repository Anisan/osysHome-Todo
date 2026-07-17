# Todo - Task Management Module

![Todo Icon](static/Todo.png)

Task management system for creating, organizing, and tracking tasks and to-do items.

## Description

The `Todo` module provides a task management system for the osysHome platform. It enables creating tasks, setting priorities, tracking completion status, and organizing tasks.

## Main Features

- ✅ **Task Creation**: Create and manage tasks
- ✅ **Task Organization**: Organize tasks by categories
- ✅ **Priority Management**: Set task priorities
- ✅ **Status Tracking**: Track task completion status
- ✅ **Search Integration**: Search tasks by title or notes
- ✅ **Widget Support**: Dashboard widget with task statistics
- ✅ **Page Support**: Standalone task management page

## Admin Panel

The module provides an admin interface for:
- Viewing tasks
- Creating and editing tasks
- Managing task status
- Organizing tasks

## API

The module provides RESTful API endpoints:
- **GET /api/Todo/...**: Task management API

## Usage

### Creating a Task

1. Navigate to Todo module
2. Click "Add Task"
3. Enter task details
4. Set priority and status
5. Save task

## Technical Details

- **Database**: SQLAlchemy models
- **API**: RESTful API endpoints
- **Search**: Full-text search support

## Version

Current version: **0.1**

## Category

App

## Actions

The module provides the following actions:
- `search` - Search tasks
- `widget` - Dashboard widget
- `page` - Standalone task page

## Requirements

- Flask
- SQLAlchemy
- osysHome core system

## Author

osysHome Team

## License

See the main osysHome project license

