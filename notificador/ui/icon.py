"""Icono de la aplicación dibujado en tiempo de ejecución (sin archivos binarios en el repositorio)."""

import wx

from .. import paths


def _draw_bell(size: int) -> wx.Bitmap:
	bmp = wx.Bitmap(size, size, 32)
	dc = wx.MemoryDC(bmp)
	dc.SetBackground(wx.Brush(wx.Colour(0, 0, 0, 0)))
	dc.Clear()
	gc = wx.GraphicsContext.Create(dc)
	s = size / 32.0
	gc.SetBrush(wx.Brush(wx.Colour(51, 133, 102)))
	gc.SetPen(wx.TRANSPARENT_PEN)
	gc.DrawRoundedRectangle(1 * s, 1 * s, 30 * s, 30 * s, 7 * s)
	gc.SetBrush(wx.Brush(wx.Colour(255, 255, 255)))
	path = gc.CreatePath()
	path.MoveToPoint(16 * s, 6 * s)
	path.AddCurveToPoint(10 * s, 6 * s, 9 * s, 11 * s, 9 * s, 15 * s)
	path.AddLineToPoint(9 * s, 20 * s)
	path.AddLineToPoint(6.5 * s, 23 * s)
	path.AddLineToPoint(25.5 * s, 23 * s)
	path.AddLineToPoint(23 * s, 20 * s)
	path.AddLineToPoint(23 * s, 15 * s)
	path.AddCurveToPoint(23 * s, 11 * s, 22 * s, 6 * s, 16 * s, 6 * s)
	path.CloseSubpath()
	gc.FillPath(path)
	gc.DrawEllipse(13 * s, 23.5 * s, 6 * s, 4 * s)
	del gc
	dc.SelectObject(wx.NullBitmap)
	return bmp


def app_icon_bundle() -> wx.IconBundle:
	bundle = wx.IconBundle()
	for size in (16, 24, 32, 48, 64):
		icon = wx.Icon()
		icon.CopyFromBitmap(_draw_bell(size))
		bundle.AddIcon(icon)
	return bundle


def tray_icon() -> wx.Icon:
	icon = wx.Icon()
	icon.CopyFromBitmap(_draw_bell(32))
	return icon


def ensure_icon_file():
	"""Guarda data/notificador.ico (lo usa el registro del AUMID). Devuelve la ruta o None."""
	path = paths.app_icon_file()
	if path.exists():
		return path
	try:
		image = _draw_bell(64).ConvertToImage()
		if image.SaveFile(str(path), wx.BITMAP_TYPE_ICO):
			return path
	except Exception:  # noqa: BLE001
		pass
	return None
