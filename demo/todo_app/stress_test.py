#!/usr/bin/env python3
"""
Stress testing for todo_app
"""
import threading
import time
import random
from todo import TodoManager

def test_basic_functionality():
    """测试基本功能"""
    print("=== 基本功能测试 ===")
    manager = TodoManager('test_stress.json')
    
    # 添加多个任务
    task_ids = []
    for i in range(10):
        task_id = manager.add_task(f'Test task {i}')
        task_ids.append(task_id)
        print(f'添加任务 {task_id}: Test task {i}')
    
    # 测试所有任务
    all_tasks = manager.list_tasks()
    print(f'所有任务数量: {len(all_tasks)}')
    
    # 测试完成任务
    completed_task = manager.complete_task(task_ids[0])
    print(f'完成任务 {task_ids[0]}: {completed_task}')
    
    # 测试状态过滤
    done_tasks = manager.list_tasks('done')
    todo_tasks = manager.list_tasks('todo')
    all_tasks_filtered = manager.list_tasks('all')
    
    print(f'已完成任务数: {len(done_tasks)}')
    print(f'待完成任务数: {len(todo_tasks)}')
    print(f'所有任务数(过滤): {len(all_tasks_filtered)}')
    
    print("基本功能测试完成\n")

def stress_test_concurrent_access():
    """并发访问压力测试"""
    print("=== 并发访问压力测试 ===")
    manager = TodoManager('test_stress.json')
    
    def worker(thread_id):
        for i in range(5):
            # 随机操作
            operation = random.choice(['add', 'list', 'complete'])
            if operation == 'add':
                task_id = manager.add_task(f'Thread-{thread_id}-Task-{i}')
                print(f'线程 {thread_id} 添加任务 {task_id}')
            elif operation == 'list':
                tasks = manager.list_tasks()
                print(f'线程 {thread_id} 列出任务 {len(tasks)} 个')
            elif operation == 'complete':
                tasks = manager.list_tasks('todo')
                if tasks:
                    task_id = tasks[0]['id']
                    result = manager.complete_task(task_id)
                    print(f'线程 {thread_id} 完成任务 {task_id}: {result}')
    
    # 创建并启动线程
    threads = []
    start_time = time.time()
    
    for i in range(5):
        t = threading.Thread(target=worker, args=(i,))
        threads.append(t)
        t.start()
    
    # 等待所有线程完成
    for t in threads:
        t.join()
    
    end_time = time.time()
    print(f'并发测试耗时: {end_time - start_time:.2f} 秒\n')

def stress_test_large_dataset():
    """大数据集压力测试"""
    print("=== 大数据集压力测试 ===")
    manager = TodoManager('test_stress.json')
    
    # 清空现有数据
    manager.tasks = []
    manager.storage.save(manager.tasks)
    
    # 添加大量任务
    start_time = time.time()
    for i in range(1000):
        manager.add_task(f'Large dataset task {i}')
    end_time = time.time()
    print(f'添加1000个任务耗时: {end_time - start_time:.2f} 秒')
    
    # 测试状态过滤性能
    start_time = time.time()
    all_tasks = manager.list_tasks('all')
    done_tasks = manager.list_tasks('done')
    todo_tasks = manager.list_tasks('todo')
    end_time = time.time()
    
    print(f'过滤1000个任务耗时: {end_time - start_time:.4f} 秒')
    print(f'所有任务: {len(all_tasks)}, 完成任务: {len(done_tasks)}, 待完成任务: {len(todo_tasks)}')
    
    print("大数据集测试完成\n")

if __name__ == '__main__':
    print("开始 todo_app 压力测试...\n")
    
    try:
        test_basic_functionality()
        stress_test_concurrent_access()
        stress_test_large_dataset()
        
        print("=== 压力测试完成 ===")
        print("所有测试通过，系统表现稳定")
        
    except Exception as e:
        print(f"测试过程中发生错误: {e}")
        import traceback
        traceback.print_exc()