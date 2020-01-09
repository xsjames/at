"""
Example command line executable for nRF91 credential management.

Operations:
    list    (--sec_tag and --cred_type optional)
    write   (--sec_tag and --cred_type and content required)
    delete  (--sec_tag and --cred_type required)
    read    (--sec_tag and --cred_type required)
    write PSK
    write certs

write PSK:
    --psk           "FOOBAR"
    --id            "nrf-foobar"
    --sec_tag       1234

write certs:
    --CA_cert       path
    --client_cert   path
    --private_key   path
    --passwd        "foobar"
    --sec_tag       1234
"""
import sys
import argparse
import time

import at
from pynrfjprog import HighLevel


FW_STARTUP_DELAY_S = 3
PREBUILT_HEX_PATH = "hex/merged.hex"


def _write_firmware(nrfjprog_probe, fw_hex):
    """Program and verify a hex file."""
    nrfjprog_probe.program(fw_hex)
    nrfjprog_probe.verify(fw_hex)
    nrfjprog_probe.reset()


def _close_and_exit(nrfjprog_api, status):
    """Close the nrfjprog connection if necessary and exit."""
    if nrfjprog_api:
        nrfjprog_api.close()
    sys.exit(status)


def _connect_to_jlink(args):
    """Connect to the debug probe."""
    api = HighLevel.API()
    api.open()
    connected_serials = api.get_connected_probes()
    if args.serial_number:
        if args.serial_number in connected_serials:
            connected_serials = [args.serial_number]
        else:
            print("error: serial_number not found ({})".format(args.serial_number))
            _close_and_exit(api, -1)
    if not connected_serials:
        print("error: no debug probes found")
        _close_and_exit(api, -1)
    if len(connected_serials) > 1:
        print("error: multiple debug probes found, use --serial_number")
        _close_and_exit(api, -1)
    probe = HighLevel.DebugProbe(api, connected_serials[0], HighLevel.CoProcessor.CP_APPLICATION)
    return (api, probe)


def _power_off_if_necessary(soc):
    """Read the modem's functional state and power it off before deleting or writing."""
    mode = soc.get_functional_mode()
    if mode == 1:
        soc.set_functional_mode(0)


def _add_and_parse_args():
    """Build the argparse object and parse the args."""
    parser = argparse.ArgumentParser(prog='cmng',
                                     description=('A command line interface for ' +
                                                  'managing nRF91 credentials.'),
                                     epilog=('WARNING: nrf_cloud relies on credentials '+
                                             'with sec_tag 16842753.'))

    parser.add_argument('operation', choices=('list', 'read', 'write', 'delete'),
                        help="operation", type=str)
    parser.add_argument('suboperation', choices=('PSK', 'certs'), nargs='?',
                        help="optional suboperation when writing", type=str)
    parser.add_argument('port', metavar='SERIAL_PORT_DEVICE',
                        help="serial port device to use for AT commands", type=str)
    parser.add_argument("--sec_tag", type=int, metavar="SECURITY_TAG",
                        help="specify sec_tag [0, 2147483647]")
    parser.add_argument("--cred_type", type=int, metavar="CREDENTIAL_TYPE",
                        help="specify cred_type [0, 5]")
    parser.add_argument("--passwd", type=str, default=None, metavar="PRIVATE_KEY_PASSWD",
                        help="specify private key password")
    parser.add_argument("--psk", type=str, metavar="PRE_SHARED_KEY",
                        help="preshared key for PSK")
    parser.add_argument("--psk_id", type=str, metavar="PSK_IDENTITY",
                        help="preshared key identity")
    parser.add_argument("--private_key", type=str, metavar="PATH_TO_PRIVATE_KEY",
                        help="read private key from file")
    parser.add_argument("--ca_cert", type=str, metavar="PATH_TO_CA_CERT",
                        help="read CA certificate from file")
    parser.add_argument("--client_cert", type=str, metavar="PATH_TO_CLIENT_CERT",
                        help="read client certificate from file")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--content", type=str, metavar="CONTENT",
                       help="specify content (i.e. key material)")
    group.add_argument("--content_path", type=str, metavar="PATH_TO_CONTENT",
                       help="read content (i.e. key material) from file")
    parser.add_argument("-s", "--serial_number", type=int, metavar="JLINK_SERIAL_NUMBER",
                        help="serial number of J-Link")
    parser.add_argument("-x", "--program_hex", action='store_true',
                        help="begin by writing prebuilt 'at_client' hex file to device")
    parser.add_argument("--program_app", type=str, metavar="PATH_TO_APP_HEX_FILE",
                        help="program specified hex file to device before finishing")
    parser.add_argument("--power_off", action='store_true',
                        help="put modem in CFUN_MODE_POWER_OFF if necessary")

    args = parser.parse_args()
    if args.sec_tag is None and args.operation != 'list':
        parser.print_usage()
        print("error: sec_tag required for all operations except listing")
        sys.exit(-1)
    if args.cred_type is None and args.operation != 'list' and not args.suboperation:
        parser.print_usage()
        print("error: cred_type required for all operations except listing")
        sys.exit(-1)
    if args.suboperation:
        if args.operation != 'write':
            parser.print_usage()
            print("error: '{}' suboperation only allowed when writing".format(args.suboperation))
            sys.exit(-1)
        if args.content or args.content_path or args.cred_type:
            parser.print_usage()
            print("error: invalid argument for suboperation")
            sys.exit(-1)
        if args.suboperation == 'PSK':
            if not args.psk or not args.psk_id:
                parser.print_usage()
                print("error: PSK suboperation requires both --psk and --psk_id arguments")
                sys.exit(-1)
            if args.passwd or args.ca_cert or args.private_key or args.client_cert:
                parser.print_usage()
                print("error: invalid argument for PSK suboperation")
                sys.exit(-1)
        else:
            if not args.passwd or not args.ca_cert or not args.private_key or not args.client_cert:
                parser.print_usage()
                print("error: certs suboperation requires --passwd, --ca_cert, " +
                      "--private_key, and --client_cert.")
                sys.exit(-1)
            if args.psk or args.psk_id:
                parser.print_usage()
                print("error: invalid argument for certs suboperation")
                sys.exit(-1)
    elif args.operation == 'write' and not (args.content or args.content_path):
        parser.print_usage()
        print("error: content or content_path is required when writing")
        sys.exit(-1)
    if args.serial_number and not (args.program_hex or args.program_app):
        parser.print_usage()
        print("error: serial number is pointless unless programming a hex file")
        sys.exit(-1)
    return args


def _read_cert_file(path):
    """Read a certificate file and return it as a string. Line endings should be <LF>."""
    with open(path, 'r') as in_file:
        content = [line.strip() for line in in_file.readlines()]
        return '\n'.join(content)


def _communicate(args):
    """Open the serial port and use the at module."""
    soc = None
    try:
        soc = at.SoC(args.port)

        if args.power_off:
            if args.operation == 'delete' or args.operation == 'write':
                _power_off_if_necessary(soc)

        if args.operation == 'list':
            result = soc.list_credentials(args.sec_tag, args.cred_type)
            if len(result) == 1:
                print(result[0])
            else:
                print(result)
        elif args.operation == 'read':
            result = soc.read_credential(args.sec_tag, args.cred_type)
            print('{!r}'.format(result))
        elif args.operation == 'delete':
            soc.delete_credential(args.sec_tag, args.cred_type)
        else:
            if args.suboperation:
                pass
            else:
                content = None
                if args.content_path:
                    content = _read_cert_file(args.content_path)
                else:
                    content = args.content
                soc.write_credential(args.sec_tag, args.cred_type, content, args.passwd)
    finally:
        if soc:
            soc.close()


def _main():
    """Parses arguments for the PPK CLI."""
    args = _add_and_parse_args()
    nrfjprog_api = None
    nrfjprog_probe = None
    try:
        if args.program_hex or args.program_app:
            nrfjprog_api, nrfjprog_probe = _connect_to_jlink(args)

        if args.program_hex:
            _write_firmware(nrfjprog_probe, PREBUILT_HEX_PATH)
            # Allow the firmware to boot.
            time.sleep(FW_STARTUP_DELAY_S)

        _communicate(args)

        if args.program_app:
            _write_firmware(nrfjprog_probe, args.program_app)

        _close_and_exit(nrfjprog_api, 0)
    except Exception as ex:
        print("error: " + str(ex))
        _close_and_exit(nrfjprog_api, -1)


if __name__ == "__main__":
    _main()