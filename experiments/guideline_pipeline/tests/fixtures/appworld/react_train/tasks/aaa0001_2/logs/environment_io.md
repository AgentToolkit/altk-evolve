
### Environment Interaction 1
------------------------------
```python
apis.todo.update_todo(id=1, status="done")
```

```
Execution failed. Traceback:
AttributeError: 'todo' has no API named 'update_todo'
```


### Environment Interaction 2
------------------------------
```python
apis.supervisor.complete_task(status="fail")
```

```
Execution successful.
```

