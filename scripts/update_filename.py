import sqlite3
import os

db='data/corpus_forge.db'
conn=sqlite3.connect(db)
c=conn.cursor()
old='ec289af82bcf4970818a1ddd595d570b_Project Corpuse Forge - Executive Summary.pdf'
new='Project Corpuse Forge - Executive Summary.pdf'
newpath=os.path.join('data','docs',new)

c.execute('UPDATE documents SET filename=?, path=? WHERE filename=?', (new, newpath, old))
conn.commit()
print(c.rowcount, 'rows updated')
conn.close()
