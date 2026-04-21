#!/usr/bin/env python3
"""
Todo manager class
"""

import json
import os
from storage import Storage

class TodoManager:
    def __init__(self, storage_file="todo_data.json"):
        """
        Initialize todo manager
        
        Args:
            storage_file (str): Storage file path
        """
        self.storage = Storage(storage_file)
        self.tasks = self.storage.load()
        
    def add_task(self, content):
        """
        Add a new todo item
        
        Args:
            content (str): Content of the todo item
            
        Returns:
            int: ID of the newly added task
        """
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Task content cannot be empty")
            
        # Get next available ID
        task_id = max([task['id'] for task in self.tasks], default=0) + 1
        
        # Create new task
        new_task = {
            'id': task_id,
            'content': content.strip(),
            'completed': False
        }
        
        # Add to task list
        self.tasks.append(new_task)
        
        # Save to storage
        self.storage.save(self.tasks)
        
        return task_id
        
    def list_tasks(self, status: str = "all"):
        """
        Get all todo items or filter by status
        
        Args:
            status (str): Filter status ('all', 'done', or 'todo'). Default is 'all'
            
        Returns:
            list: List containing filtered tasks
        """
        if status == "all":
            return self.tasks.copy()  # Return copy to prevent external modification
        elif status == "done":
            return [task for task in self.tasks if task['completed']]
        elif status == "todo":
            return [task for task in self.tasks if not task['completed']]
        else:
            raise ValueError("Invalid status parameter. Must be 'all', 'done', or 'todo'")
        
    def complete_task(self, task_id):
        """
        Mark specified todo item as completed
        
        Args:
            task_id (int): ID of task to complete
            
        Returns:
            bool: True if task exists and was marked as completed, False otherwise
        """
        for task in self.tasks:
            if task['id'] == task_id:
                task['completed'] = True
                self.storage.save(self.tasks)
                return True
        return False
        
    def delete_task(self, task_id):
        """
        Delete specified todo item
        
        Args:
            task_id (int): ID of task to delete
            
        Returns:
            bool: True if task exists and was deleted, False otherwise
        """
        original_length = len(self.tasks)
        self.tasks = [task for task in self.tasks if task['id'] != task_id]
        
        if len(self.tasks) < original_length:
            self.storage.save(self.tasks)
            return True
        return False