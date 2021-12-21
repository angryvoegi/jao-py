import pandas as pd
from lxml import etree
from datetime import datetime
from .definitions import ParseDataSubject
from typing import Union


def _infer_and_convert_type(s):
    try:
        v = s[s.first_valid_index()]
    except KeyError:
        # no valid index so whole thing is nan. simply return as float nan type
        return s.astype(float)

    if type(v) != str:
        return s
    t = None
    try:
        int(v)
        t = int
    except:
        try:
            float(v)
            t = float
        except:
            if v == 'true' or v == 'false':
                return s.map({'true': True, 'false': False}).astype(bool)
    if t is not None:
        try:
            return s.astype(t)
        except:
            pass
    return s.fillna('')


def _parse_utility_tool_xml(xml: Union[bytes, str], t: ParseDataSubject) -> pd.DataFrame:
    """
    parses the xml coming out of the utility tool

    :param xml: string or bytes of the xml data
    :param t: which type to parse, choose from "MaxExchanges", "MaxNetPositions", "Ptdfs"
    :return:
    """
    if type(xml) == str:
        xml_b = xml.encode('UTF-8')
    elif type(xml) == bytes:
        xml_b = xml
    else:
        raise ValueError("xml should be provided in either bytes or string format")

    tree = etree.fromstring(xml_b)
    # the node name is the singular of the type
    node_name = t.value.strip('s')
    # get all the column names by checking all the children tags of a node
    column_names = [x.tag for x in tree.xpath(t + '/' + node_name)[0].getchildren()]

    # now get all data fast with xpath by retrieving the text for every tag, the order is guaranteed the same
    # so we can stitch them back together later
    data = []

    for c_name in column_names:
        data.append([str(x) for x in tree.xpath(f'{t}/{node_name}/{c_name}/node()')])

    df = pd.DataFrame(zip(*data), columns=column_names)
    for c in column_names[2:]:
        df[c] = _infer_and_convert_type(df[c])

    df['TIMESTAMP_CET'] = df.apply(lambda row: datetime.strptime(row['Date'], '%Y-%m-%dT00:00:00').replace(hour=int(row['CalendarHour'])-1), axis=1)

    df.drop(columns=['Date', 'CalendarHour'], inplace=True)

    return df


def _parse_utilitytool_cwe_netpositions(xml: Union[bytes, str]) -> pd.DataFrame:
    """
    parses the xml coming out of the netposition endpoint of the excell utilitytool

    :param xml: string or bytes of the xml data
    :return:
    """

    if type(xml) == str:
        xml_b = xml.encode('UTF-8')
    elif type(xml) == bytes:
        xml_b = xml
    else:
        raise ValueError("xml should be provided in either bytes or string format")

    tree = etree.fromstring(xml_b)

    data = {
        'date': tree.xpath("ns:NetPositionData/ns:CalendarDate/node()", namespaces={'ns':'http://tempuri.org/'}),
        'hour': tree.xpath("ns:NetPositionData/ns:CalendarHour/node()", namespaces={'ns':'http://tempuri.org/'}),
    }
    for zone in ['AT', 'NL', 'BE', 'DE', 'FR', 'ALBE', 'ALDE']:
        data[zone] = tree.xpath(f"ns:NetPositionData/ns:{zone}/node()", namespaces={'ns':'http://tempuri.org/'})

    df = pd.DataFrame.from_dict(data, orient='columns')
    df = df[['date', 'hour', 'AT', 'NL', 'BE', 'DE', 'FR', 'ALBE', 'ALDE']]
    df[['AT', 'NL', 'BE', 'DE', 'FR', 'ALBE', 'ALDE']] = \
        df[['AT', 'NL', 'BE', 'DE', 'FR', 'ALBE', 'ALDE']].astype('float')
    df = pd.DataFrame(df)

    df['TIMESTAMP_CET'] = df.apply(
        lambda row: datetime.strptime(row['date'], '%Y-%m-%dT00:00:00').replace(hour=int(row['hour']) - 1),
        axis=1)
    df.drop(columns=['date', 'hour'], inplace=True)

    return df


def _parse_maczt_final_flowbased_domain(df: pd.DataFrame, zone='NL') -> pd.DataFrame:
    """
    extracts the MACZT numbers from the final flowbased domain dataframe
    for now only NL zone is supported

    :param df:
    :return:
    """
    if zone != 'NL':
        raise NotImplementedError

    # select only relevant columns
    df = df[['OutageName', 'OutageEIC', 'CriticalBranchName', 'CriticalBranchEIC',
             'Presolved', 'RemainingAvailableMargin', 'Fmax', 'Fref', 'AMR', 'MinRAMFactor', 'MinRAMFactorJustification']]

    # if there is a default parameter day there is an empty dataframe. stop further processing
    if len(df) == 0:
        return df


    # filter on cnecs that have a valid dutch justification string and are not lta
    # make sure to make copy to prevent slice errors later
    df = df[df['MinRAMFactorJustification'].str.contains('MACZTtarget').fillna(False)]
    df = df[~(df['CriticalBranchName'].str.contains('LTA_corner'))]
    df = pd.DataFrame(df)

    df['MCCC_PCT'] = 100 * df['RemainingAvailableMargin'] / df['Fmax']

    df[['MNCC_PCT', 'LF_CALC_PCT', 'LF_ACCEPT_PCT', 'MACZT_TARGET_PCT']] = \
        df['MinRAMFactorJustification'].str.extract(
            r'MNCC = (?P<MNCC_PCT>.*)%;LFcalc = (?P<LF_CALC_PCT>.*)%;LFaccept = (?P<LF_ACCEPT_PCT>.*)%;MACZTtarget = (?P<MACZT_TARGET_PCT>.*)%')
    df[['MCCC_PCT', 'MNCC_PCT', 'LF_CALC_PCT', 'LF_ACCEPT_PCT', 'MACZT_TARGET_PCT']] = \
        df[['MCCC_PCT', 'MNCC_PCT', 'LF_CALC_PCT', 'LF_ACCEPT_PCT', 'MACZT_TARGET_PCT']].astype(float)

    df['MACZT_PCT'] = df['MCCC_PCT'] + df['MNCC_PCT']
    df['LF_SUB_PCT'] = (df['LF_CALC_PCT'] - df['LF_ACCEPT_PCT']).clip(lower=0)
    df['MACZT_MIN_PCT'] = df['MACZT_TARGET_PCT'] - df['LF_SUB_PCT']
    df['MACZT_MARGIN'] = df['MACZT_PCT'] - df['MACZT_MIN_PCT']

    df.drop(columns=['MinRAMFactorJustification'], inplace=True)
    df.rename(columns={
        'OutageName': 'CO',
        'OutageEIC': 'CO_EIC',
        'CriticalBranchName': 'CNE',
        'CriticalBranchEIC': 'CNE_EIC'
    })

    # there is only two decimals statistical relevance here so round it at that
    df[['MCCC_PCT', 'MACZT_PCT', 'LF_SUB_PCT', 'MACZT_MIN_PCT', 'MACZT_MARGIN']] = df[['MCCC_PCT', 'MACZT_PCT', 'LF_SUB_PCT', 'MACZT_MIN_PCT', 'MACZT_MARGIN']].round(2)

    return df


def _parse_suds_tradingdata(data, subject: str, nested: bool = False) -> pd.DataFrame:
    """

    :param data: suds.sudsobject.TradingData object
    :param subject: string of the type that needs to be parsed
    :return: resulting pandas dataframe
    """
    data_parsed = []
    if nested:
        data_raw = data[subject][subject.strip('s')]
    else:
        data_raw = data[subject]
    for obj in data_raw:
        obj = dict(obj)
        if 'Date' in obj:
            obj['timestamp'] = obj['Date'].replace(hour=obj['CalendarHour'] - 1)
            del obj['Date']
        else:
            obj['timestamp'] = obj['CalendarDate'].replace(hour=obj['CalendarHour'] - 1)
            del obj['CalendarDate']

        del obj['CalendarHour']
        data_parsed.append(obj)

    return pd.DataFrame(data_parsed).set_index('timestamp').tz_localize('Europe/Amsterdam')