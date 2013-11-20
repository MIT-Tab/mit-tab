#Copyright (C) 2011 by Julia Boortz and Joseph Lynch

#Permission is hereby granted, free of charge, to any person obtaining a copy
#of this software and associated documentation files (the "Software"), to deal
#in the Software without restriction, including without limitation the rights
#to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#copies of the Software, and to permit persons to whom the Software is
#furnished to do so, subject to the following conditions:

#The above copyright notice and this permission notice shall be included in
#all copies or substantial portions of the Software.

#THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
#THE SOFTWARE.

from tab.models import *
import smtplib
from email.mime.text import MIMEText
import threading
from functools import partial
import time

def text():
    print "sending texts"
    email_from = "mitdebatetab@gmail.com"
    username = "mitdebatetab"
    password = "mitdebatetab"
    round_to_text = (TabSettings.objects.get(key="cur_round").value)-1
    pairings = Round.objects.filter(round_number = round_to_text)
    threads = []
    for p in pairings:
        text = " Gov: " + p.gov_team.name + ", Opp: " + p.opp_team.name + ", Judge: " + p.judge.name + ", Room: " + p.room.name
        people = []
        for i in list(p.gov_team.debaters.all()):
            people += [i]
        for i in list(p.opp_team.debaters.all()):
            people += [i]
        people += [p.judge]
        for person in people:
            #print person
            email = None
            phone = str(person.phone)
            #print "string of phone"
            #print phone
            phone = phone.replace("-","")
            phone = phone[0:10]
            if person.provider == "Alltel":
                email = phone + "@message.alltel.com"
            elif person.provider == "AT&T": #tested
                email = phone + "@txt.att.net"
            elif person.provider == "Boost Mobile":
                email = phone + "@myboostmobile.com"
            elif person.provider == "Nextel (now Sprint Nextel)":
                email = phone + "@messaging.nextel.com"
            elif person.provider == "Sprint PCS (now Sprint Nextel)":
                email = phone + "@messaging.sprintpcs.com"
            elif person.provider == "T-mobile": #tested
                email = phone + "@tmomail.net"
            elif person.provider == "US Cellular":
                email = phone + "@email.uscc.net"
            elif person.provider == "Verizon": #tested
                email = phone + "@vtext.com"
            elif person.provider == "Virgin Mobile USA":
                email = phone + "@vmobl.com"
            if email != None:
                msg = MIMEText(text)
                msg['Subject'] = "Round " + str(round_to_text) + " pairings"
                msg['From'] = email_from
                msg['To'] = email
                try:
                    threads.append(threading.Thread(target=partial(send_email,
                                                                   username, 
                                                                   password,
                                                                   email_from,
                                                                   email,
                                                                   msg)))
                except Exception as e:
                    print "Could not email: ", person, " because :", e
    
    # Send the texts and wait for them to finish
    print "Sending all texts at ", time.ctime()
    for thread in threads:
        try:
            thread.start()
        except Exception as e:
            print "Could not send a text message, failed to start thread"
            print e
    print "Waiting for texts to be sent at ", time.ctime()
    for thread in threads:
        try:
            thread.join()
        except Exception as e:
            print e
    print "All texts done at ", time.ctime()
    # While nice, these won't play well with dieing
    #map(lambda x: x.start(), threads)
    #map(lambda x: x.join(), threads)


def send_email(user, password, email_from, email, msg):
    print "Sending round info email to: %s" % email
    s = smtplib.SMTP('smtp.gmail.com:587')
    s.starttls()
    s.login(user, password)
    s.sendmail(email_from, email, msg.as_string())
    s.quit()
                
        
        
    
