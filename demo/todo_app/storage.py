#!/usr/bin/env python3
"""
Storage manager class
Handles persistence of todo items
"""

import json
import os

class Storage:
    def __init__(self, file_path):
        """
        Initialize storage manager
        
        Args:
            file_path (str): Data file path
        """
        self.file_path = file_path
        
    def load(self):
        """
        Load todo items from file
        
        Returns:
            list: Loaded task list, empty list if file doesn't exist or is corrupted
        """
        if not os.path.exists(self.file_path):
            return []
            
        try:
            with open(self.file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Ensure data format is correct
                if isinstance(data, list):
                    return data
                else:
                    return []
        except (json.JSONDecodeError, IOError):
            # Return empty list if file is corrupted or cannot be read
            return []
            
    def save(self, tasks):
        """
        Save todo items to file
        
        Args:
            tasks (list): Tasks to save
        """
        try:
            with open(self.file_path, 'w', encoding='utf-8') as f:
                json.dump(tasks, f, ensure_ascii=False, indent=2)
        except IOError as e:
            raise IOError("Failed to save data: %s" % e)