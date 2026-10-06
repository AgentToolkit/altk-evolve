
### Environment Interaction 1
------------------------------
```python
todos = apis.todo.show_todos(status="open")
print(todos)
```

```
[{"id": 1, "text": "buy milk"}]
```


### Environment Interaction 2
------------------------------
```python
apis.supervisor.complete_task(answer="buy milk")
```

```
Execution successful.
```

