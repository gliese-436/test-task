from pdfminer.layout import LAParams, LTTextBox, LTLine, LTRect
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfinterp import PDFResourceManager
from pdfminer.pdfinterp import PDFPageInterpreter
from pdfminer.converter import PDFPageAggregator
from pdf2image import convert_from_path
from pyzbar.pyzbar import decode
import os
import glob
import json

def read_code128_barcodes_from_pdf(pdf_file, page_layout):
    try:
        os.mkdir('poppler_temp')
    except OSError as error:
        pass
    
    pageid = page_layout.pageid
    size = (page_layout.width, page_layout.height)
    
    images_from_path = convert_from_path(pdf_file, first_page=pageid, last_page=pageid, size=size, output_folder='poppler_temp', poppler_path='poppler/bin')
    barcodes = []
    for image in images_from_path:
        barcodes += decode(image)

    return barcodes

def parse_pdf(path_to_pdf):

    file = open(path_to_pdf, 'rb')
    rsrcmgr = PDFResourceManager()
    laparams = LAParams()
    device = PDFPageAggregator(rsrcmgr, laparams=laparams)
    interpreter = PDFPageInterpreter(rsrcmgr, device)
    pages = PDFPage.get_pages(file)
    
    # Будем считать что должна быть только 1 страница
    # Если страниц несколько, эту часть нужно будет обернуть в цикл
    # и возвращемые данные должны будут быть в массиве
    data = { 'file': path_to_pdf }
    rects = []
    lines = []
    texts = []
    barcodes = {}

    interpreter.process_page(pages.__next__())
    layout = device.get_result()
    barcodes_data = read_code128_barcodes_from_pdf(path_to_pdf, layout)
    
    for item in layout:
        # Отберем только прямоугольники с рамкой
        # будем считать что такой прямоугольник не является страницей или контект боксом
        if isinstance(item, LTRect) and item.stroke:
            rects.append(item)
        if isinstance(item, LTLine):
            lines.append(item)
        if isinstance(item, LTTextBox):
            texts.append(item)
           
    # Преобразуем линии в блоки штрихкодов для определния их боксов и дальнейшей привязки их к текстовым блокам
    for item in list(lines):
        # Используем Y0 как ключ для конкретного штрихкода на странице
        # так как все линии с тем же Y0 находятся в той же оси X
        key = int(item.y0)
        if key in barcodes:
            barcode = barcodes[key]
            if item.x0 < barcode['x0']: barcode['x0'] = item.x0
            if item.y0 < barcode['y0']: barcode['y0'] = item.y0
            if item.x1 > barcode['x1']: barcode['x1'] = item.x1
            if item.y1 > barcode['y1']: barcode['y1'] = item.y1
            barcode['width'] = barcode['x1'] - barcode['x0']
            barcode['height'] = barcode['y1'] - barcode['y0']
        else:
            barcodes[key] = {
                'x0': item.x0,
                'y0': item.y0,
                'x1': item.x1,
                'y1': item.y1,
                'width': item.x1 - item.x0,
                'height': item.y1 - item.y0,
                'value': ''
            }
        lines.remove(item)
        
    # Сопоставим данные штрихкодов с их блоками
    for bc_rect in barcodes.values():
        for bc_data in list(barcodes_data):
            # Высота почти не отличается, но ширина сильно разнится, пусть дельта будет меньше 10%
            hPct = abs((int(bc_rect['height']) - bc_data.rect.height) / bc_data.rect.height) * 100
            wPct = abs((int(bc_rect['width']) - bc_data.rect.width) / bc_data.rect.width) * 100
            if hPct < 10 and wPct < 10:
                bc_rect['value'] = bc_data.data.decode()
                barcodes_data.remove(bc_data)
         
    # В первой итерации пытаемся распределить тексты
    for item in list(texts):
        text = item.get_text()
        if ':' in text:
            # Пробуем привязать прямоугольник (текстбокс) к конкретному полю
            # будем считать что он принадлежит этому тексту если их боксы находятся
            # друг от друга на расстоянии меньше 1px
            found_textbox = None
            for rect in list(rects):
                if abs(rect.y1 - item.y0) < 1 and abs(rect.x0 - item.x0) < 1:
                    found_textbox = {
                        'x0': rect.x0,
                        'y0': rect.y0,
                        'x1': rect.x1,
                        'y1': rect.y1,
                        'width': rect.width,
                        'height': rect.height
                    }
                    rects.remove(rect)
                    break
                
            # Пробуем привязать прямоугольник (штрихкод) к конкретному полю
            # будем считать что он принадлежит этому тексту если их боксы находятся
            # друг от друга на расстоянии меньше 5px
            found_barcode = None
            for key in list(barcodes):
                value = barcodes[key]
                if abs(item.y1 - value['y0']) < 5 and abs(item.x0 - value['x0']) < 5:
                    found_barcode = value
                    barcodes.__delitem__(key)
                    break
                
            parts = text.split(':')
            key = parts[0].strip()
            value = parts[1].strip()
            data[key] = {
                'text': value,
                'x0': item.x0,
                'y0': item.y0,
                'x1': item.x1,
                'y1': item.y1,
                'width': item.width,
                'height':item.height
            }
            
            if found_textbox:
                data[key]['textbox'] = found_textbox
            if found_barcode:
                data[key]['barcode'] = found_barcode
            texts.remove(item)
            
    # Пробежимся по текстам, которые не смогли распределить в первой итерации
    for item in list(texts):
        # Если высота бокса больше 10 и верхняя граница в 10px от границы медиабокса будем считать что это заголовок
        # (На самом деле тут можно сделать разные проверки, например по размеру шрифта или просто по индексу элемента, он тут равен 0)
        if item.height > 10 and abs(item.y1 - layout.height) < 10:
            data['header'] = {
                'text': item.get_text().strip(),
                'x0': item.x0,
                'y0': item.y0,
                'x1': item.x1,
                'y1': item.y1,
                'width': item.width,
                'height':item.height
            }
            texts.remove(item)
            continue
        
        # Определим к какому текстбоксу относится текст, если такой есть то этот текст будет значением ключевого поля
        # Этот подход не оптимизирован, так как проходит в каждой итерации по всем спарсеным данным для нахождения текстбоксов
        for value in data.values():
            if 'textbox' in value:
                if (value['textbox']['x0'] < item.x0 and value['textbox']['x1'] > item.x1 and
                   value['textbox']['y0'] < item.y0 and value['textbox']['y1'] > item.y1):
                    value['text'] = item.get_text().strip()
                    texts.remove(item)
                    break
                
    # Последняя итерация по текстам, запишем их в неизвестные ключи
    for item in list(texts):
        data['Unk' + item.index] = {
            'text': item.get_text(),
            'x0': item.x0,
            'y0': item.y0,
            'x1': item.x1,
            'y1': item.y1,
            'width': item.width,
            'height':item.height
        }
        texts.remove(item)      
       
    print(json.dumps(data, indent=4))
    return data

def compare(standard, sample):

    try:
        assert len(standard) == len(sample)
    except Exception:
        print(f"ERROR: mismath keys length in file: {sample['file']}")

    for std_key, std_value in standard.items():
        try:
            assert std_key in sample
        except Exception:
            print(f"ERROR: key {std_key} absents in file: {sample['file']}")

        if std_key != 'file':
            try:
                smp_value = sample[std_key]
                assert (std_value['x0'] == smp_value['x0'] and
                        std_value['y0'] == smp_value['y0'] and
                        std_value['y1'] == smp_value['y1'])
            except Exception:
                print(f"ERROR: coordinates of key {std_key} don't match in file: {sample['file']}")

        if 'textbox' in std_value:
            try:
                assert 'textbox' in sample[std_key]
            except Exception:
                print(f"ERROR: textbox in key {std_key} isn't found in file: {sample['file']}")

        if 'barcode' in std_value:
            try:
                assert 'barcode' in sample[std_key]
                try:
                    assert sample[std_key]['barcode']['value'] == sample[std_key]['text']
                except Exception:
                    print(f"ERROR: {std_key} doesn't match barcode in file: {sample['file']}")
            except Exception:
                print(f"ERROR: barcode in key {std_key} isn't found in file: {sample['file']}")

    return True

standard_path = 'test_task.pdf'
samples_path = 'samples/*.pdf'

if(__name__ == '__main__'):
    standard = parse_pdf(standard_path)
    samples = []
    for path in glob.glob(samples_path, recursive=True):
        samples.append(parse_pdf(path))
    for sample in samples:
        compare(standard, sample)