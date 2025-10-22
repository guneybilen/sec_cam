# ack_gui.py

import os
import tkinter as tk
from trace import acknowledge

def on_ack():
    acknowledge()
    try:
        os.remove("/tmp/gui_ack_active")
    except FileNotFoundError:
        pass
    root.destroy()


root = tk.Tk()
root.title("Critical Alert")
root.geometry("300x100")

label = tk.Label(root, text="Critical error active!", font=("Arial", 14))
label.pack(pady=10)

button = tk.Button(root, text="Acknowledge (Roger)", command=on_ack, font=("Arial", 12))
button.pack()

root.mainloop()
