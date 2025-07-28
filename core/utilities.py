def to_float(num: str):
    try:
        return float(num.replace(',', '.'))
    except Exception:
        return 0

def is_float(num: str):
    try:
        float(num.replace(',', '.'))
        return True
    except Exception:
        return False

def to_boolean(bol: str):
    return bol == 'TRUE'

def perc_to_float(perc: str):
    if perc == '':
        return 0
    return int(perc.replace('%', '')) / 100
