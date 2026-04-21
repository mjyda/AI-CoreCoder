import sys
sys.path.append('.')
from todo import TodoManager

# Test basic functionality
manager = TodoManager('test.json')
print('Created TodoManager successfully')

# Test adding task
task_id = manager.add_task('Test task 1')
print('Added task with ID:', task_id)

# Test listing tasks
tasks = manager.list_tasks()
print('Tasks:', tasks)

# Test completing task
result = manager.complete_task(1)
print('Completed task result:', result)

# Test deleting task
result = manager.delete_task(1)
print('Deleted task result:', result)

print('All tests passed')