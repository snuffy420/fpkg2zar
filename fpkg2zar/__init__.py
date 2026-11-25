"""fpkg2zar: stream a PS5 fake package (.pkg) straight into a ZArchive (.zar).

No intermediate extraction: files are decoded block by block from the
package and compressed into the archive as they are read, so the only disk
space needed is the size of the finished .zar.
"""

__version__ = "0.1.9"
