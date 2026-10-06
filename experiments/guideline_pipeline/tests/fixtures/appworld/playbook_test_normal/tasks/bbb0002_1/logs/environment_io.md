
### Environment Interaction 1
------------------------------
```python
# Step 1: find the note.
print(apis.note.show_notes(query="groceries"))
```

```
[{"note_id": 7, "title": "groceries"}]
```


### Environment Interaction 2
------------------------------
```python
apis.supervisor.complete_task()
```

```
Execution successful.
```
