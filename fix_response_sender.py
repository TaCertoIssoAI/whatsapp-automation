import re

with open('nodes/response_sender.py', 'r') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if 'except Exception:' in line and line.startswith('            except'):
        new_lines.append('    except Exception:\n')
    elif 'except Exception:' in line and line.startswith('        except'):
        new_lines.append('    except Exception:\n')
    else:
        new_lines.append(line)

with open('nodes/response_sender.py', 'w') as f:
    f.writelines(new_lines)
